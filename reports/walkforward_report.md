# Walk-forward validációs riport — testületi mérési kapu

*Készült: 2026-07-15. Protokoll: expanding-window (2011–2025), a hiperparaméterek és a warm_nights felvétele minden tanítóablakon belül, beágyazott LOYO-val választva — a tesztév utáni információ sehol nem szerepel.*

## Fő számok — a régi (LOYO) és az új (walk-forward) módszertan EGYÜTT

A walk-forward szám a szigorúbb: csak a múltból jósol, a trend extrapolál (nem interpolál). **Ez a szám az, amire üzleti döntést érdemes alapozni.** A régi LOYO-szám összevetésül szerepel.

| Termény | LOYO (régi) | Walk-forward v1 | Walk-forward v2 (agro) | Naiv trend (WF) | Győztes |
|---|---|---|---|---|---|
| búza | 0.529 | 0.583 | 0.573 | 0.621 | **v2** |
| kukorica | 1.397 | 1.512 | 1.519 | 1.948 | **v1** |
| őszi árpa | 0.545 | 0.574 | 0.574 | 0.669 | **v2** |

*(t/ha; v2 = termény-bázisú/plafonozott GDD + EDD hőstressz-intenzitás; a warm_nights változót a belső kiválasztás ablakonként dönti el.)*

## Vármegyei vs. országos hiba

A fenti RMSE **vármegye-szintű** (ebből számoljuk a vármegyei sávot). Az **országos** becslés tipikus hibája kisebb, mert a vármegyei tévedések aggregáláskor részben kioltják egymást — a főoldali/PDF „a becslés tipikus tévedése ±X%” EZT az országos számot közli:

| Termény | Vármegyei WF-RMSE | Országos WF-RMSE | Országos hiba (%) |
|---|---|---|---|
| búza | 0.573 | 0.444 | 8.7% |
| kukorica | 1.512 | 1.348 | 20.9% |
| őszi árpa | 0.574 | 0.432 | 8.4% |

*(Az országos hiba %-a itt a minta-átlaghozamhoz viszonyít; a főoldal a mindenkori trend-szinthez, ezért ott pár tizeddel eltérhet.)*

## Sáv-kalibráció (M2): empirikus kvantilisek, vármegyei zsugorított szórás

| Termény | q10 | q90 | Lefedettség (össz.) | Évek >=80% lefedettséggel |
|---|---|---|---|---|
| búza | -1.26 | 1.26 | 80% | 10/15 (95% CI: 43–91%) |
| kukorica | -1.24 | 1.27 | 80% | 9/15 (95% CI: 35–85%) |
| őszi árpa | -1.30 | 1.18 | 80% | 7/15 (95% CI: 21–72%) |

Az aszimmetrikus q10/q90 a hozameloszlás balra ferdeségét tükrözi (az aszályos lehúzás nagyobb, mint a felfelé meglepetés) — a korábbi szimmetrikus Gauss-sáv ezt csonkolta.

## Stressz-év riport (agrártudósi kikötés)

A walk-forward ablakból kimaradó/lefedett szélsőévek LOYO-alapú összevetése (RMSE, t/ha) — a v2 készlet a szélsőségekben sem lehet rosszabb érdemben:

| Termény | Év | v1 | v2 |
|---|---|---|---|
| búza | 2003 | 0.450 | 0.450 |
| búza | 2007 | 0.340 | 0.340 |
| búza | 2012 | 0.336 | 0.336 |
| búza | 2022 | 0.925 | 0.925 |
| kukorica | 2003 | 0.607 | 0.699 |
| kukorica | 2007 | 0.931 | 1.712 |
| kukorica | 2012 | 1.311 | 1.305 |
| kukorica | 2022 | 2.795 | 2.789 |
| őszi árpa | 2003 | 0.341 | 0.341 |
| őszi árpa | 2007 | 0.534 | 0.534 |
| őszi árpa | 2012 | 0.452 | 0.452 |
| őszi árpa | 2022 | 0.682 | 0.682 |

## Döntés

A győztes feature-készlet kerül élesbe terményenként; az élő sáv a walk-forward empirikus kvantiliseiből számolódik (aszimmetrikus, vármegyénként eltérő szélességű). A kommunikált „tipikus tévedés” mostantól a walk-forward RMSE.