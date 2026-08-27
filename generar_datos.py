"""
Genera los JSON de proyeccion que consume el dashboard.

    pip install soccerdata pandas
    python generar_datos.py

Escribe datos/premier.json y datos/laliga.json con las tasas por 90 minutos
de cada jugador: pases completados, tiros y tiros al arco.

Por que un generador y no fetch desde el navegador
--------------------------------------------------
GitHub Pages sirve archivos estaticos. La pagina no puede raspar FBref sola:
no hay CORS, y cada visita gastaria peticiones contra el rate limit. Python
precalcula, el frontend solo pinta.

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
PREVIA = "2526"          # se mezcla mientras la temporada nueva sea corta
MIN_MINUTOS = 180        # menos de dos partidos completos es ruido

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


def leer(liga_fb, temporada):
    fb = sd.FBref(liga_fb, temporada)
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


def main():
    SALIDA.mkdir(exist_ok=True)

    for slug, liga_fb, nombre in LIGAS:
        print(f"\n=== {nombre} ===")
        print("  temporada actual...")
        nueva = leer(liga_fb, TEMPORADA)
        time.sleep(8)
        print("  temporada previa...")
        vieja = leer(liga_fb, PREVIA)

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
                  "equipos": equipos}
        ruta = SALIDA / f"{slug}.json"
        ruta.write_text(json.dumps(salida, ensure_ascii=False, separators=(",", ":")))
        print(f"  {ruta} · {len(equipos)} equipos · "
              f"{sum(len(v) for v in equipos.values())} jugadores")

    print("\nListo. Sube la carpeta datos/ al repo.")
    time.sleep(0)


if __name__ == "__main__":
    main()
