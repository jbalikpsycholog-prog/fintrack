"""
Uzivatelsky prijemne spusteni FinTracku - bez cerneho okna prikazove radky.

Po spusteni (dvojklikem na Spustit_FinTrack.vbs, nebo primo na tento soubor
pres pythonw.exe) se aplikace rozbehne na pozadi, automaticky se otevre
prohlizec na http://localhost:8000, a v systemove liste (u hodin, dole
vpravo) se objevi maly ikonka FinTracku. Pres tuhle ikonku (klik pravym
tlacitkem) jde FinTrack kdykoliv znovu otevrit v prohlizeci, nebo ukoncit.

Zavreni karty v prohlizeci FinTrack neukonci - beh na pozadi je zamerny
(stejne jako u jinych "tray" aplikaci), aby stranka byla vzdy hned po
ruce. Ukoncit se da jen pres tu ikonku dole v system tray.
"""

import os
import sys
import time
import socket
import threading
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# pythonw.exe nema zadnou konzoli, takze sys.stdout/stderr casto je None -
# jakykoliv print() nebo nezachycena chyba by pak shodila cely skript bez
# jakekoli stopy. Presmerujeme radeji vse do log souboru vedle aplikace,
# aby se dalo pripadne zpetne zjistit, co se stalo.
LOG_PATH = os.path.join(BASE_DIR, "fintrack_tray.log")
if sys.stdout is None or sys.stderr is None:
    _log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log
    sys.stderr = _log

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://localhost:{PORT}"


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def show_message(text, title="FinTrack"):
    """Zobrazi jednoduche systemove okenko se zpravou (funguje i bez konzole
    a bez dalsich knihoven - pouziva primo Windows API pres ctypes)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)  # MB_ICONINFORMATION
    except Exception:
        log("(nepodarilo se zobrazit systemove okenko se zpravou: " + text + ")")


def is_port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 61, 61), fill=(37, 99, 235, 255))
    d.ellipse((16, 16, 47, 47), fill=(255, 255, 255, 255))
    d.ellipse((23, 23, 40, 40), fill=(37, 99, 235, 255))
    return img


def main():
    import uvicorn
    import pystray

    log("Startuji FinTrack...")

    # Pokud uz na portu 8000 nekdo posloucha (typicky uz bezici FinTrack -
    # napr. z predchoziho spusteni, ktere jeste nebylo ukonceno pres ikonku
    # v system tray), nezakladame druhou, zbytecnou (a nefunkcni, port uz je
    # obsazeny) instanci. Misto toho proste otevreme prohlizec na tu jiz
    # bezici a rovnou skoncime - bez druhe ikonky, ktera by jen matla.
    if is_port_in_use(HOST, PORT):
        log("Port 8000 uz je obsazeny - FinTrack pravdepodobne uz bezi, jen otevirem prohlizec.")
        webbrowser.open(URL)
        show_message(
            "FinTrack uz běží (najdeš ho jako ikonku dole u hodin) - "
            "otevírám ho znovu v prohlížeči.\n\n"
            "Pokud tam žádnou ikonku nevidíš, zkus rozbalit skryté ikonky "
            "šipkou „^“ vedle hodin."
        )
        return

    config = uvicorn.Config("main:app", host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    def open_browser():
        for _ in range(80):  # az cca 20 vterin cekani na start serveru
            if server.started:
                break
            time.sleep(0.25)
        if server.started:
            log("Server bezi, otevirem prohlizec.")
            webbrowser.open(URL)
        else:
            log("Server se nepodarilo spustit v ocekavanem case.")
            show_message(
                "FinTrack se nepodařilo spustit.\n\n"
                "Zkus prosím počítač restartovat a spustit FinTrack znovu. "
                "Pokud problém přetrvá, podívej se do souboru fintrack_tray.log "
                "vedle aplikace, nebo se ozvi - podíváme se na to spolu."
            )

    threading.Thread(target=open_browser, daemon=True).start()

    def on_open(icon, item):
        webbrowser.open(URL)

    def on_quit(icon, item):
        log("Ukoncuji FinTrack (uzivatel zvolil Ukoncit).")
        server.should_exit = True
        server_thread.join(timeout=10)
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Otevřít FinTrack", on_open, default=True),
        pystray.MenuItem("Ukončit FinTrack", on_quit),
    )
    icon = pystray.Icon("fintrack", make_icon_image(), "FinTrack OSVČ", menu)

    try:
        icon.run()
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
        log("FinTrack ukoncen.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log("CHYBA pri behu FinTracku:\n" + traceback.format_exc())
