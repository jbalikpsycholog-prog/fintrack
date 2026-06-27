# FinTrack OSVC

**Webova aplikace pro sledovani vydaju a prijmu OSVC** – import CSV vypisu z Ceske sporitelny.

Aplikace bezi lokalne na vasem pocitaci v prohlizeci (zadne cloud, zadna registrace).

---

## Co aplikace umi

- Import CSV vypisu z Ceske sporitelny
- - Automaticka kategorizace transakci
  - - Prehled vydaju a prijmu po kategoriich
    - - Rozpocty a reporty
      - - Sprava uctenek
       
        - ---

        ## Pozadavky

        - **Python 3.10 nebo novejsi** – stahni z [python.org](https://www.python.org/downloads/)
        -   - Behem instalace **zatrhni "Add Python to PATH"**!
            - - Internetove pripojeni (jen pri prvnim spusteni, pro stazeni zavislosti)
             
              - ---

              ## Jak stahnout aplikaci

              1. Klikni na zelene tlacitko **Code** na teto strance
              2. 2. Zvol **Download ZIP**
                 3. 3. Rozbal stazeny ZIP do slozky (napr. `C:\FinTrack` nebo `~/FinTrack`)
                   
                    4. ---
                   
                    5. ## Spusteni na Windows
                   
                    6. 1. Otevri slozku s aplikaci
                       2. 2. Dvakrat klikni na soubor **`setup.bat`**
                          3. 3. Pockas, az se nainstaluje (poprve muze trvat 1-2 minuty)
                             4. 4. Automaticky se otevre prohlizec na adrese `http://localhost:8000`
                               
                                5. > Pokud Windows zobrazi varovani "Neznamy vydavatel", klikni na **Dalsi informace** a pak **Spustit**.
                                   >
                                   > Pri dalsim spusteni opet spust `setup.bat` – bude rychlejsi.
                                   >
                                   > ---
                                   >
                                   > ## Spusteni na Mac / Linux
                                   >
                                   > 1. Otevri Terminal
                                   > 2. 2. Prejdi do slozky s aplikaci:
                                   >    3.    ```
                                   >             cd cesta/k/fintrack
                                   >             ```
                                   >          3. Povol spusteni skriptu (jen jednou):
                                   >          4.    ```
                                   >                   chmod +x start.sh
                                   >                   ```
                                   >                4. Spust aplikaci:
                                   >                5.    ```
                                   >                         ./start.sh
                                   >                         ```
                                   >                      5. Otevre se prohlizec na adrese `http://localhost:8000`
                                   >                  
                                   >                      6. ---
                                   >                  
                                   >                      7. ## Ukonceni aplikace
                                   >                  
                                   >                      8. - Stiskni **Ctrl+C** v okne terminalu / prikazoveho radku
                                   >
                                   > ---
                                   >
                                   > ## Reseni problemu
                                   >
                                   > | Problem | Reseni |
                                   > |---|---|
                                   > | `Python neni nainstalovan` | Stahni Python z python.org, pri instalaci zatrhni "Add Python to PATH" |
                                   > | `Port 8000 je obsazen` | Zavre jinou aplikaci nebo restartuj pocitac |
                                   > | Prohlizec se neotevre | Rucne zadej do prohlizece: `http://localhost:8000` |
                                   > | Aplikace se nezpusti | Zkontroluj, ze jsi v te spravne slozce s aplikaci |
                                   >
                                   > ---
                                   >
                                   > ## Technicke info (pro zajemce)
                                   >
                                   > Aplikace je postavena na Pythonu + FastAPI + SQLite. Data jsou ulozena lokalne v souboru `fintrack.db` ve stejne slozce jako aplikace.
