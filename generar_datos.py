"""
Genera los JSON de proyeccion que consume el dashboard.

    pip install soccerdata pandas
    python generar_datos.py

Escribe datos/premier.json y datos/laliga.json con:
  - tasas por 90 de cada jugador (pases completados, tiros, tiros al arco)
  - el calendario de los proximos partidos
  - un factor defensivo por equipo, para ajustar la proyeccion segun el rival

Sobre el factor de rival
------------------------
Es el cociente entre lo que un equipo concede y el promedio de la liga.
Si el Getafe concede 8.4 tiros por partido y la liga promedia 12.1, su factor
es 0.69: enfrentarlo baja la proyeccion de tiros un 31%.

Es un ajuste crudo. No sabe de estilos, de si el rival juega replegado ni de
como se comporta yendo ganando. Mueve la aguja unos pocos puntos, no la
reinventa. Sigue mandando la variable de minutos.

Corre esto en tu maquina, no en GitHub Actions: las IP de los runners son
compartidas y FBref las bloquea seguido.

Limite de FBref: 10 peticiones por minuto, bloqueo de ~24h si lo pasas.
soccerdata cachea en disco, asi que la segunda corrida no vuelve a pedir.
"""
import json, time
from pathlib import Path

import pandas as pd
import soccerdata as sd

TEMPORADA = "2627"
PREVIA = "2526"
MIN_MINUTOS = 180
MAX_PARTIDOS = 20        # cuantos partidos futuros publicar

LIGAS = [
    ("premier", "ENG-Premier League", "Premier League"),
    ("laliga",  "ESP-La Liga",        "LaLiga"),
]

SALIDA = Path("datos")


def col(df, *pistas):
    """FBref devuelve MultiIndex y cambia de forma seguido. Busca por texto."""
    plano = {}
    for c in df.columns:
        etiqueta = " ".join(str(x) for x in c) if isinstance(c, tuple) else str(c)
        plano[etiqueta.lower()] = c
    for p in pistas:
        for etiqueta, real in plano.items():
            if p.lower() in etiqueta:
                return real
    raise KeyError(f"No encontre {pistas}. Hay: {list(plano)[:25]}")


def jugadores(fb):
    est = fb.read_player_season_stats(stat_type="standard").reset_index()
    tir = fb.read_player_season_stats(stat_type="shooting").reset_index()
    pas = fb.read_player_season_stats(stat_type="passing").reset_index()

    d = est[[col(est, "team"), col(est, "player"),
             col(est, "playing time min", "min")]].copy()
    d.columns = ["equipo", "jugador", "min"]

    t = tir[[col(tir, "player"), col(tir, "standard sh", "shots total"),
             col(tir, "standard sot", "shots on target")]].copy()
    t.columns = ["jugador", "tiros", "arco"]

    p = pas[[col(pas, "player"), col(pas, "total cmp", "passes completed")]].copy()
    p.columns = ["jugador", "pases"]

    d = d.merge(t, on="jugador", how="left").merge(p, on="jugador", how="left")
    for c in ("min", "tiros", "arco", "pases"):
        d[c] = pd.to_numeric(d[c], errors="coerce").fillna(0)
    return d


def mezclar(nueva, vieja):
    """Peso por minutos: la temporada nueva manda conforme acumula partidos."""
    d = nueva.merge(vieja, on=["equipo", "jugador"], how="left", suffixes=("", "_p"))
    peso = (d["min"] / (d["min"] + 450)).clip(0, 1)
    for c in ("tiros", "arco", "pases"):
        r_n = d[c] / d["min"].replace(0, 1) * 90
        r_v = (d[c + "_p"] / d["min_p"].replace(0, 1) * 90).fillna(r_n)
        d[c + "_90"] = (r_n * peso + r_v * (1 - peso)).round(2)
    return d


def factores_rival(fb):
    """Cuanto concede cada equipo respecto al promedio de la liga."""
    try:
        vs = fb.read_team_season_stats(stat_type="shooting",
                                       opponent_stats=True).reset_index()
    except Exception as e:
        print(f"  Sin stats de rival ({e}). Factor = 1.00 para todos.")
        return {}

    eq = col(vs, "team")
    sh = col(vs, "standard sh", "shots total")
    pj = col(vs, "90s", "playing time")

    d = vs[[eq, sh, pj]].copy()
    d.columns = ["equipo", "sh", "pj"]
    for c in ("sh", "pj"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d["pj"] > 0]
    d["por90"] = d["sh"] / d["pj"]
    prom = d["por90"].mean()
    if not prom or pd.isna(prom):
        return {}
    return {r.equipo: round(float(r.por90 / prom), 3) for r in d.itertuples()}


def calendario(fb):
    """Proximos partidos sin jugar."""
    try:
        s = fb.read_schedule().reset_index()
    except Exception as e:
        print(f"  Sin calendario ({e}).")
        return []

    f_fecha = col(s, "date")
    f_loc = col(s, "home_team", "home")
    f_vis = col(s, "away_team", "away")
    try:
        f_sc = col(s, "score")
        sin_jugar = s[s[f_sc].isna()]
    except KeyError:
        sin_jugar = s

    sin_jugar = sin_jugar.sort_values(f_fecha).head(MAX_PARTIDOS)
    return [{"fecha": str(r[f_fecha])[:10],
             "local": str(r[f_loc]), "visitante": str(r[f_vis])}
            for _, r in sin_jugar.iterrows()]


def main():
    SALIDA.mkdir(exist_ok=True)

    for slug, liga_fb, nombre in LIGAS:
        print(f"\n=== {nombre} ===")
        fb_act = sd.FBref(liga_fb, TEMPORADA)

        print("  jugadores, temporada actual...")
        nueva = jugadores(fb_act)
        time.sleep(8)
        print("  jugadores, temporada previa...")
        vieja = jugadores(sd.FBref(liga_fb, PREVIA))
        time.sleep(8)
        print("  factores de rival...")
        factores = factores_rival(fb_act)
        time.sleep(8)
        print("  calendario...")
        partidos = calendario(fb_act)

        d = mezclar(nueva, vieja)
        d = d[d["min"] >= MIN_MINUTOS]

        equipos = {}
        for eq, g in d.groupby("equipo"):
            g = g.sort_values("pases_90", ascending=False).head(18)
            equipos[eq] = [{
                "n": r.jugador, "min": int(r.min),
                "p90": float(r.pases_90), "t90": float(r.tiros_90),
                "a90": float(r.arco_90),
            } for r in g.itertuples()]

        salida = {"liga": nombre, "temporada": TEMPORADA,
                  "generado": pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                  "equipos": equipos, "factores": factores, "partidos": partidos}
        ruta = SALIDA / f"{slug}.json"
        ruta.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")))
        print(f"  {ruta} · {len(equipos)} equipos · "
              f"{sum(len(v) for v in equipos.values())} jugadores · "
              f"{len(partidos)} partidos")

    print("\nListo. Sube la carpeta datos/ al repo.")


if __name__ == "__main__":
    main()
