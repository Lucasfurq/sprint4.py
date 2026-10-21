"""
Simulador de Energia Integrada - Sprint 4 Goodwe
Solar (FV) + Bateria + Recarga de Veiculo Eletrico, com gestao inteligente.

Compara dois cenarios para 1 dia (passo de 15 min):
  A) Convencional : FV direto na casa, sem bateria, VE carrega assim que chega.
  B) Inteligente  : FV + bateria + VE gerenciado por prioridade de excedente
                    solar e tarifa horaria (logica de um inversor hibrido).

ATENCAO: dados de geracao/consumo sao SIMULADOS (modelo sintetico, semente fixa).
Tarifas e fator de emissao sao valores ILUSTRATIVOS - ajuste com sua concessionaria.
"""
import csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------- Parametros do sistema (ajustaveis) ----------------
DT = 0.25                      # h (15 min)
N = int(24 / DT)               # 96 passos
T = np.arange(N) * DT          # hora do dia

PV_KWP = 5.0                   # potencia FV instalada (kWp)
BAT_KWH = 10.0                 # capacidade util da bateria (kWh)
BAT_PMAX = 5.0                 # potencia max carga/descarga (kW)
BAT_ETA = 0.95                 # eficiencia por sentido
BAT_SOC0 = 1.0                 # kWh iniciais
BAT_RESERVA_PONTA = 6.0        # kWh a garantir antes da ponta (18h)

EV_PMAX = 7.4                  # kW (wallbox monofasica 32 A)
EV_NECESSIDADE = 20.0          # kWh a recarregar
EV_CHEGADA, EV_SAIDA = 9.0, 23.75   # janela conectado (h) - dia de home office

# Tarifa horaria ilustrativa (R$/kWh) e valor da energia exportada
def tarifa(h):
    if 18 <= h < 21:
        return 1.60            # ponta
    if (17 <= h < 18) or (21 <= h < 22):
        return 0.95            # intermediario
    return 0.65                # fora de ponta
TARIFA_EXPORT = 0.40           # R$/kWh (credito simplificado)
FATOR_CO2 = 0.06               # kgCO2/kWh da rede (ilustrativo, SIN Brasil)

# ---------------- Perfis sinteticos ----------------
rng = np.random.default_rng(42)

def perfil_fv():
    sol = np.clip(np.sin(np.pi * (T - 6) / 12), 0, None)   # nascer 6h, por 18h
    nuvens = np.clip(1 - 0.25 * np.abs(rng.normal(0, 1, N)), 0.5, 1.0)
    return PV_KWP * 0.80 * sol * nuvens                     # 80% performance ratio

def perfil_casa():
    base = 0.35 + 0.05 * rng.normal(0, 1, N)
    manha = 0.9 * np.exp(-((T - 7.5) ** 2) / 1.0)
    almoco = 0.5 * np.exp(-((T - 12.5) ** 2) / 1.0)
    noite = 1.9 * np.exp(-((T - 19.5) ** 2) / 2.2)
    return np.clip(base + manha + almoco + noite, 0.2, None)

PV = perfil_fv()
CASA = perfil_casa()


# ---------------- Cenario A: convencional ----------------
def simula_convencional():
    falta = EV_NECESSIDADE
    res = []
    for i, h in enumerate(T):
        ev = 0.0
        if EV_CHEGADA <= h < EV_SAIDA and falta > 1e-9:
            ev = min(EV_PMAX, falta / DT)
            falta -= ev * DT
        carga = CASA[i] + ev
        direto = min(PV[i], carga)
        imp = carga - direto
        exp = PV[i] - direto
        res.append(dict(h=h, pv=PV[i], casa=CASA[i], ev=ev, bat=0.0, soc=0.0,
                        imp=imp, exp=exp))
    return res


# ---------------- Cenario B: inteligente ----------------
def simula_inteligente():
    falta = EV_NECESSIDADE
    soc = BAT_SOC0
    res = []
    for i, h in enumerate(T):
        pv, casa = PV[i], CASA[i]
        t = tarifa(h)
        conectado = EV_CHEGADA <= h < EV_SAIDA
        ev = 0.0
        bat = 0.0      # + carga / - descarga (lado AC)

        liquido = pv - casa          # excedente solar apos a casa

        if liquido >= 0:
            exced = liquido
            # 1) bateria ate a reserva de ponta
            if soc < BAT_RESERVA_PONTA:
                p = min(exced, BAT_PMAX, (BAT_RESERVA_PONTA - soc) / (DT * BAT_ETA))
                bat += p; exced -= p
            # 2) VE com excedente solar
            if conectado and falta > 1e-9 and exced > 0:
                p = min(exced, EV_PMAX, falta / DT)
                ev += p; exced -= p
            # 3) resto do excedente enche a bateria
            if exced > 0 and soc + bat * DT * BAT_ETA < BAT_KWH:
                p = min(exced, BAT_PMAX - bat,
                        (BAT_KWH - soc - bat * DT * BAT_ETA) / (DT * BAT_ETA))
                p = max(p, 0)
                bat += p; exced -= p
            exp = exced
            imp = 0.0
        else:
            deficit = -liquido
            exp = 0.0
            # bateria cobre a casa fora do horario de menor tarifa
            if t > 0.65 and soc > 0.2:
                p = min(deficit, BAT_PMAX, soc * BAT_ETA / DT)
                bat -= p; deficit -= p
            imp = deficit

        # VE a partir da rede: "o mais tarde possivel", so fora de ponta.
        # Complementa so quando a energia restante ja exige todos os passos
        # fora de ponta que sobram ate a saida (prioriza o sol).
        if conectado and falta - ev * DT > 1e-9 and t == 0.65:
            passos_fp = sum(1 for hh in T if h <= hh < EV_SAIDA and tarifa(hh) == 0.65)
            passos_nec = (falta - ev * DT) / (EV_PMAX * DT)
            if passos_nec >= passos_fp - 1:
                p = min(EV_PMAX - ev, (falta - ev * DT) / DT)
                ev += p; imp += p
        # estado da bateria
        if bat >= 0:
            soc += bat * DT * BAT_ETA
        else:
            soc += bat * DT / BAT_ETA
        soc = min(max(soc, 0.0), BAT_KWH)
        falta -= ev * DT
        res.append(dict(h=h, pv=pv, casa=casa, ev=ev, bat=bat, soc=soc,
                        imp=imp, exp=exp))
    return res, falta


# ---------------- Metricas ----------------
def metricas(res):
    imp = sum(r["imp"] for r in res) * DT
    exp = sum(r["exp"] for r in res) * DT
    pv = sum(r["pv"] for r in res) * DT
    consumo = sum(r["casa"] + r["ev"] for r in res) * DT
    ev = sum(r["ev"] for r in res) * DT
    custo = sum(r["imp"] * tarifa(r["h"]) * DT for r in res) \
        - exp * TARIFA_EXPORT
    ponta = sum(r["imp"] * DT for r in res if 18 <= r["h"] < 21)
    pico = max(r["imp"] for r in res)
    return dict(
        fv_kwh=pv, consumo_kwh=consumo, ev_kwh=ev, importado_kwh=imp,
        exportado_kwh=exp, importado_ponta_kwh=ponta, pico_rede_kw=pico,
        autoconsumo_pct=100 * (pv - exp) / pv if pv else 0,
        autossuficiencia_pct=100 * (1 - imp / consumo) if consumo else 0,
        custo_rs=custo, co2_rede_kg=imp * FATOR_CO2,
    )


def grafico(a, b, caminho):
    fig, axs = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    for ax, res, titulo in zip(axs, (a, b),
                               ("A) Convencional (sem bateria, VE sem gestao)",
                                "B) Inteligente (FV + bateria + VE gerenciado)")):
        ax.fill_between(T, [r["pv"] for r in res], alpha=0.3, color="gold",
                        label="Geracao FV")
        ax.plot(T, [r["casa"] for r in res], color="tab:blue", label="Casa")
        ax.plot(T, [r["ev"] for r in res], color="tab:green", label="VE")
        ax.plot(T, [r["imp"] for r in res], color="tab:red", lw=2,
                label="Importado da rede")
        ax.axvspan(18, 21, color="red", alpha=0.07, label="Ponta")
        ax.set_title(titulo)
        ax.set_ylabel("kW")
        ax.grid(alpha=0.3)
    axs[1].plot(T, [r["soc"] for r in b], color="purple", ls="--",
                label="SoC bateria (kWh)")
    axs[1].set_xlabel("Hora do dia")
    axs[0].legend(ncol=3, fontsize=8)
    axs[1].legend(ncol=3, fontsize=8)
    plt.tight_layout()
    plt.savefig(caminho, dpi=130)


def main():
    a = simula_convencional()
    b, falta = simula_inteligente()
    ma, mb = metricas(a), metricas(b)

    linhas = [
        ("Geracao FV (kWh)", "fv_kwh"), ("Consumo total (kWh)", "consumo_kwh"),
        ("Energia ao VE (kWh)", "ev_kwh"), ("Importado da rede (kWh)", "importado_kwh"),
        ("Exportado (kWh)", "exportado_kwh"),
        ("Importado na ponta (kWh)", "importado_ponta_kwh"),
        ("Pico de importacao (kW)", "pico_rede_kw"),
        ("Autoconsumo FV (%)", "autoconsumo_pct"),
        ("Autossuficiencia (%)", "autossuficiencia_pct"),
        ("Custo liquido do dia (R$)", "custo_rs"),
        ("CO2 da rede (kg)", "co2_rede_kg"),
    ]
    print(f"{'Metrica':34s}{'A) Convenc.':>14s}{'B) Intelig.':>14s}")
    for nome, k in linhas:
        print(f"{nome:34s}{ma[k]:14.2f}{mb[k]:14.2f}")
    print(f"\nEnergia nao entregue ao VE no cenario B: {max(falta, 0):.2f} kWh")
    red_custo = 100 * (ma["custo_rs"] - mb["custo_rs"]) / ma["custo_rs"]
    print(f"Reducao de custo: {red_custo:.1f}%")

    with open("resultados.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["metrica", "convencional", "inteligente"])
        for nome, k in linhas:
            w.writerow([nome, round(ma[k], 2), round(mb[k], 2)])
    grafico(a, b, "grafico_comparativo.png")
    print("Gerados: resultados.csv e grafico_comparativo.png")


if __name__ == "__main__":
    main()
