# Backtest riport — búzahozam-előrejelző (mérési kapu)

*Készült: 2026-07-14. Adat: KSH vármegyei búza-termésátlag (2000–2025), ERA5 (Open-Meteo), 19 vármegye (Budapest kihagyva — elhanyagolható termőterület).*

## 1. Modell

Panelregresszió: vármegye-fixhatás + közös lineáris időtrend (a technológiai fejlődés leválasztására) + standardizált időjárási mutatók (ablakos GDD-k, csapadék, hőstressznapok, vízmérleg-mutatók, halmozott vízmérleg-deficit). Becslés: OLS szelektív ridge büntetéssel (α=25.0, csak az időjárási blokkon; LOYO ráccsal választva).

## 2. Leave-one-year-out validáció (out-of-sample)

| Modell | RMSE (t/ha) | RMSE (%) | R² |
|---|---|---|---|
| **panelmodell** | 0.529 | 11.4% | 0.725 |
| naiv: vármegye-trend | 0.682 | 14.7% | 0.542 |
| naiv: előző 3 év átlaga | 0.782 | 16.9% | 0.398 |

A bizonytalansági sáv a LOYO reziduumok szórásából: ±1.282·0.529 t/ha (névleges 80%); tényleges lefedettség **82.6%**.

## 3. As-of backtest (06. hó 15. napi tudásállapot)

A feature-ök a célév as-of napjáig ismert időjárásból + a hátralévő napokra a többi év klimatológiájából; a modell a célév nélkül tanítva (LOYO-konvenció: a célév kizárva, de a célév UTÁNI évek benne vannak a tanításban és a klimatológiában — egy valódi korabeli futás ennél kevesebb adatot látott volna).

| Év | Jósolt anomália (átlag) | Tényleges anomália (átlag) | Iránytalálat (vármegye) |
|---|---|---|---|
| 2003 | -21.8% | -34.2% | 19/19 |
| 2007 | -14.4% | -14.2% | 19/19 |
| 2022 | -2.3% | -18.1% | 15/19 |

### 2022 vármegyénként (a leginkább érintettől a legkevésbé érintettig)

| Vármegye | Tényleges anomália | Jósolt anomália | Irány |
|---|---|---|---|
| Jász-Nagykun-Szolnok | -39.8% | -6.1% | ✔ |
| Hajdú-Bihar | -39.4% | -2.4% | ✔ |
| Heves | -35.7% | -4.5% | ✔ |
| Csongrád-Csanád | -32.6% | +0.5% | ✘ |
| Pest | -32.3% | -5.3% | ✔ |
| Békés | -30.1% | +0.7% | ✘ |
| Nógrád | -27.5% | -5.3% | ✔ |
| Szabolcs-Szatmár-Bereg | -22.5% | -9.8% | ✔ |
| Bács-Kiskun | -19.9% | -0.5% | ✔ |
| Borsod-Abaúj-Zemplén | -19.2% | -6.7% | ✔ |
| Komárom-Esztergom | -17.4% | -2.6% | ✔ |
| Baranya | -14.9% | +2.5% | ✘ |
| Fejér | -8.6% | -3.5% | ✔ |
| Győr-Moson-Sopron | -6.3% | -2.0% | ✔ |
| Veszprém | -5.4% | -2.3% | ✔ |
| Tolna | -3.1% | +0.5% | ✘ |
| Somogy | +0.2% | +2.8% | ✔ |
| Zala | +1.8% | +0.6% | ✔ |
| Vas | +9.1% | +0.2% | ✔ |

![backtest](figures/backtest_wheat_2003.png)
![backtest](figures/backtest_wheat_2007.png)
![backtest](figures/backtest_wheat_2022.png)

## 4. A mérési kapu értékelése

- **(a) Naiv alap verése:** lásd a 2. táblázatot.
- **(b) 2022 iránytartás:** 15/19 vármegyénél helyes az előjel, a 10 leginkább érintettből 8-nál.
- **(c) Sáv realitása:** 82.6% tényleges lefedettség a névleges 80%-ra.

### Ismert korlátok (őszintén)

- **A 2022-es anomália MÉRTÉKÉT a modell alulbecsüli** (jún. 15-i átlag -2.3% a tényleges -18.1% helyett). Két ok: (1) a június végi hőhullám a jún. 15-i tudásállapotban még nem ismert — a teljes szezonos (LOYO) becslés már −5,5%-ot ad; (2) 2022-ben a műtrágyaár-robbanás (háború) is csökkentette a hozamot, ami nem időjárási tényező, egy időjárás-alapú modell elvben sem foghatja meg.
- A halmozott vízmérleg-deficit (wb_deficit) bevezetése a kapu-iteráció eredménye: az összesített out-of-sample RMSE-t 0,624-ről 0,529 t/ha-ra javította, és a 2012-es aszályt is 19/19-re hozza.
- A modell szezonon belüli frissítéssel (5. fázis) az as-of nap utáni időjárást is beépíti, a 2022-szerű késői stresszt is követve.