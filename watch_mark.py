import os
import sys
import time
import subprocess
from pathlib import Path

IGNORE_DIRS = {
    'venv', '.idea', '.vscode', '__pycache__', 'knowledge', 'mark_chrome_extension'
}
IGNORE_FILES = {
    'leads.csv', 'action_log.csv', 'arbitrage_proposal.txt', 'freelancer_network.csv'
}
MONITORED_EXTENSIONS = {
    '.py', '.html', '.css', '.js', '.json'
}

def get_monitored_files(root_dir: Path):
    files = {}
    for path in root_dir.rglob('*'):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.name in IGNORE_FILES:
            continue
        if path.is_file() and path.suffix in MONITORED_EXTENSIONS:
            try:
                files[path] = path.stat().st_mtime
            except OSError:
                pass
    return files

def main():
    root_dir = Path(__file__).resolve().parent
    print('=========================================')
    print('       MARK AUTOMATIC RESTART WATCHER    ')
    print('=========================================')
    print(f'[*] Monitoring directory: {root_dir}')
    print('[*] Watching for changes to restart Mark.py automatically.')
    print('=========================================\n')
    
    python_exe = sys.executable
    
    current_files = get_monitored_files(root_dir)
    proc = None
    
    try:
        proc = subprocess.Popen([python_exe, 'Mark.py'], cwd=str(root_dir))
        
        while True:
            time.sleep(1)
            
            new_files = get_monitored_files(root_dir)
            changed = False
            
            if set(new_files.keys()) != set(current_files.keys()):
                changed = True
                print('\n[Watcher] File list changed (file added or removed).')
            else:
                for path, mtime in new_files.items():
                    if current_files.get(path) != mtime:
                        changed = True
                        print(f'\n[Watcher] Change detected in: {path.relative_to(root_dir)}')
                        break
                        
            if changed:
                current_files = new_files
                if proc and proc.poll() is None:
                    print('[Watcher] Terminating current Mark instance...')
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        print('[Watcher] Force killing Mark instance...')
                        proc.kill()
                print('[Watcher] Launching a new instance of Mark...')
                proc = subprocess.Popen([python_exe, 'Mark.py'], cwd=str(root_dir))
                
    except KeyboardInterrupt:
        print('\n[Watcher] Exiting monitor...')
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        sys.exit(0)

if __name__ == "__main__":
    main()
