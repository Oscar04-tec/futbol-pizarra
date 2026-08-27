"""
Red de pases y mapa de tiros a partir de StatsBomb Open Data.

    python red_pases.py 3869685 Argentina

Saca dos cosas para un equipo en un partido:
  1. La red de pases hasta la primera sustitucion, con centralidades.
  2. Los tiros con xG, quien asistio y en que termino cada uno.

La convencion de cortar en la primera sustitucion no es capricho: despues
cambia el once y la red deja de describir una estructura estable.

Los datos son gratis y publicos: github.com/statsbomb/open-data
"""
import sys, json, statistics as st
from collections import defaultdict
from urllib.request import urlopen

import networkx as nx

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"


def bajar(partido):
    with urlopen(f"{BASE}/events/{partido}.json") as r:
        return json.load(r)


def minuto_corte(eventos, equipo):
    """Minuto de la primera sustitucion del equipo. Si no hubo, todo el partido."""
    for e in eventos:
        if e["type"]["name"] == "Substitution" and e["team"]["name"] == equipo:
            return e["minute"]
    return 200


def red_de_pases(eventos, equipo, corte):
    """Devuelve (posiciones, aristas) usando solo pases completados antes del corte."""
    ubicaciones = defaultdict(list)
    aristas = defaultdict(int)

    for e in eventos:
        if e["type"]["name"] != "Pass" or e["team"]["name"] != equipo:
            continue
        if e["minute"] >= corte:
            continue
        # Sin 'outcome' significa pase completado. Es al reves de lo intuitivo.
        if "outcome" in e["pass"]:
            continue
        receptor = e["pass"].get("recipient")
        if not receptor:
            continue

        emisor = e["player"]["name"]
        ubicaciones[emisor].append(e["location"])
        aristas[tuple(sorted([emisor, receptor["name"]]))] += 1

    # Mediana, no promedio: un solo pase largo no debe arrastrar el nodo.
    posiciones = {
        jug: (st.median(p[0] for p in pts), st.median(p[1] for p in pts))
        for jug, pts in ubicaciones.items()
    }
    return posiciones, aristas


def centralidades(aristas):
    g = nx.Graph()
    for (a, b), n in aristas.items():
        # Para caminos mas cortos, muchos pases = menos "distancia".
        g.add_edge(a, b, peso=n, distancia=1 / n)

    return {
        "intermediacion": nx.betweenness_centrality(g, weight="distancia"),
        "vector_propio": nx.eigenvector_centrality(g, weight="peso", max_iter=1000),
        "grado": dict(g.degree(weight="peso")),
    }


def tiros(eventos, equipo):
    """Tiros del equipo con xG, resultado y quien dio el pase clave."""
    por_id = {e["id"]: e for e in eventos}
    salida = []

    for e in eventos:
        if e["type"]["name"] != "Shot" or e["team"]["name"] != equipo:
            continue
        s = e["shot"]
        asistio = None
        if "key_pass_id" in s:
            kp = por_id.get(s["key_pass_id"])
            if kp:
                asistio = kp["player"]["name"]

        salida.append({
            "minuto": e["minute"],
            "jugador": e["player"]["name"],
            "xg": round(s["statsbomb_xg"], 4),
            "desde": e["location"],
            "hacia": s["end_location"],
            "resultado": s["outcome"]["name"],
            "jugada": s["type"]["name"],
            "parte": s["body_part"]["name"],
            "asistio": asistio,
            # Cuantos rivales habia entre el tirador y la porteria.
            "rivales_delante": sum(
                1 for j in s.get("freeze_frame", [])
                if not j["teammate"] and j["location"][0] > e["location"][0]
            ),
        })
    return salida


def main():
    partido = sys.argv[1] if len(sys.argv) > 1 else "3869685"
    equipo = sys.argv[2] if len(sys.argv) > 2 else "Argentina"

    eventos = bajar(partido)
    corte = minuto_corte(eventos, equipo)
    posiciones, aristas = red_de_pases(eventos, equipo, corte)
    c = centralidades(aristas)
    ts = tiros(eventos, equipo)

    print(f"{equipo} · partido {partido} · red hasta el minuto {corte}")
    print(f"{len(posiciones)} jugadores, {sum(aristas.values())} pases completados\n")

    print("Jugador                        Pases  Intermed.  V.propio")
    orden = sorted(c["grado"], key=lambda j: -c["intermediacion"][j])
    for j in orden:
        print(f"{j[:28]:<30} {c['grado'][j]:>5}  {c['intermediacion'][j]:>8.3f}"
              f"  {c['vector_propio'][j]:>8.3f}")

    print(f"\nParejas con mas pases entre si:")
    for (a, b), n in sorted(aristas.items(), key=lambda x: -x[1])[:5]:
        print(f"  {n:>3}  {a.split()[-1]} - {b.split()[-1]}")

    print(f"\n{len(ts)} tiros · xG total {sum(t['xg'] for t in ts):.2f}")
    porjug = defaultdict(lambda: [0, 0.0])
    for t in ts:
        porjug[t["jugador"]][0] += 1
        porjug[t["jugador"]][1] += t["xg"]
    for j, (n, xg) in sorted(porjug.items(), key=lambda x: -x[1][1]):
        print(f"  {j[:28]:<30} {n:>2} tiros   xG {xg:.2f}")

    with open(f"salida_{partido}_{equipo}.json", "w") as f:
        json.dump({"posiciones": posiciones, "aristas": {f"{a}|{b}": n for (a, b), n in aristas.items()},
                   "centralidades": c, "tiros": ts}, f, ensure_ascii=False, indent=1)
    print(f"\nJSON guardado.")


if __name__ == "__main__":
    main()
