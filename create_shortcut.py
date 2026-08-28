import os
from pathlib import Path
from win32com.client import Dispatch

shortcut_name = "Start Mark.lnk"
workspace_path = Path(__file__).resolve().parent
desktop_path = Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
startup_path = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

def create_shortcut(target_path: Path) -> None:
    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(str(target_path))
    shortcut.TargetPath = str(workspace_path / "start_mark.bat")
    shortcut.WorkingDirectory = str(workspace_path)
    shortcut.WindowStyle = 1
    shortcut.Description = "Launch Mark with its dashboard and voice assistant."
    shortcut.IconLocation = str(workspace_path / "Mark.py")
    shortcut.save()

for folder in [desktop_path, startup_path]:
    folder.mkdir(parents=True, exist_ok=True)
    create_shortcut(folder / shortcut_name)

print(f"Desktop shortcut created: {desktop_path / shortcut_name}")
print(f"Startup shortcut created: {startup_path / shortcut_name}")
print("Mark will launch automatically when Windows starts.")
