# ZenseHome MQTT Bridge (Home Assistant Add-on)

Denne add-on forbinder en **ZenseHome PC-boks** til **Home Assistant** via **MQTT Discovery**, så dine Zense-enheder dukker op som `light` entiteter i Home Assistant.

ZenseHome-enheder kommunikerer via el-installationens 230V ledningsnet (powerline), og PC-boksen fungerer som gateway mellem LAN/PC og enhederne i installationen.

## Hvad gør add-on’en?

- Opretter `light` entiteter i Home Assistant via MQTT Discovery (`homeassistant/light/.../config`)
- Sender kommandoer til PC-boksen via TCP (standard port 10001)
- Mapper Home Assistant kommandoer til Zense ASCII API:
  - **ON/OFF** → `Set` (bridge bruger 0/100 for at passe til HA’s lysmodel)
  - **Brightness** → `Fade 0..100`
- Udgiver state tilbage til Home Assistant (retained), så HA ikke “spammer” kommandoer ved usikker state
- Valgfri polling (fx hver 5–10 min) for at fange ændringer lavet på fysiske vægtryk

## Krav

- Home Assistant OS eller Supervised (Supervisor/Add-ons)
- MQTT broker (fx Mosquitto add-on eller ekstern broker)
- ZenseHome PC-boks tilsluttet LAN og strøm (PC-boksen er gateway til enhederne)

## Installation (Add-on)

1. Home Assistant → **Indstillinger → Add-ons → Add-on butik**
2. Menu (⋮) → **Repositories** → tilføj repo URL
3. Tilføj **ZenseHome MQTT Bridge** → **Installér**
4. Gå til **Konfiguration** og indtast dine værdier (IP, port, ID/kode, osv.)
5. Start add-on’en

## Konfiguration

I add-on konfigurationen (UI) sætter du bl.a.:

- `zense_ip`: IP-adresse på PC-boksen
- `zense_port`: normalt `10001`
- `zense_code`: PC-boks ID/kode til login
- `mqtt_host/mqtt_port/mqtt_user/mqtt_pass`: MQTT broker (kan auto-fyldes hvis du bruger Mosquitto service)
- `state_poll_sec`: polling interval i sekunder (fx `600` = 10 min, `300` = 5 min)
- `level_on_window_sec`: lille “vindue” der gør at HA’s efterfølgende `ON` ikke overskriver en `brightness/set`

### Hvorfor findes `level_on_window_sec`?
Home Assistant sender ofte både:
- `.../brightness/set` (niveau)
- `.../set` = `ON` (fordi “brightness” også betyder “tænd”)

Bridge ignorerer `ON`, hvis den lige har modtaget en brightness for samme enhed – så bliver det til **Fade til niveauet** i stedet for at `ON` “trumfer”.

## Polling (fysiske vægtryk)

Hvis du tænder/slukker på et fysisk vægtryk, får Home Assistant ikke nødvendigvis besked (ingen push-events i ASCII API). Derfor kan add-on’en poll’e status ved at kalde `Get {id}` på kendte enheder og opdatere state.

Anbefaling:
- 5–10 min (300–600 sek) er ofte nok til “wall switch sync” uden at belaste PC-boksen.

## Fejlfinding

### “Broken pipe” / forbindelsen ryger
PC-boksen kan lukke TCP forbindelsen, og add-on’en reconnecter automatisk. Hvis det sker ofte:
- Tjek at du ikke samtidig har et PC-program eller andet script forbundet
- Reducér command rate (høj `cmd_gap_sec`) hvis nødvendigt

### Login lockout
Ved for mange forkerte loginforsøg kan PC-boksen låse logins i en periode. Kontrollér at `zense_code` er korrekt.

### Enheder dukker ikke op i Home Assistant
- Tjek MQTT broker virker
- Tjek add-on logs for discovery output
- Genstart add-on (den laver discovery ved opstart)

## Sikkerhed

- Kør kun bridge på dit lokale netværk.
- Del ikke din PC-boks ID/kode eller MQTT credentials offentligt.

## Disclaimer

Dette er et uofficielt community-projekt og er ikke associeret med ZenseHome.

## Licens

MIT (se `LICENSE` fil).
