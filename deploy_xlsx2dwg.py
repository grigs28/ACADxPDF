"""Deploy xlsx2dwg tools to remote workers via paramiko."""
import paramiko, os

FILES = [
    "tools/xlsx2dwg_net/xlsx2json.py",
    "tools/xlsx2dwg_net/run_xlsx2dwg.py",
    "tools/xlsx2dwg_net/XlsxToDwg/bin/Release/XlsxToDwg.dll",
    "acad2pdf/xlsx2dwg_worker.py",
]

BASE = r"C:\opt\ACADxPDF"
REMOTE_BASE = "C:/opt/ACADxPDF"
WORKERS = ["192.168.93.201", "192.168.93.202"]
USER = "grigs"
PASS = "Slnwg123$"


def deploy(host):
    print(f"=== Deploying to {host} ===")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=USER, password=PASS)

    sftp = ssh.open_sftp()

    for f in FILES:
        local = os.path.join(BASE, f.replace("/", os.sep))
        remote = f"{REMOTE_BASE}/{f}"
        if not os.path.isfile(local):
            print(f"  {f} MISSING (local)")
            continue

        # ensure remote dir exists
        rdir = "/".join(remote.split("/")[:-1])
        parts = rdir.split("/")
        for i in range(1, len(parts) + 1):
            try:
                sftp.mkdir("/".join(parts[:i]))
            except IOError:
                pass

        sftp.put(local, remote)
        print(f"  {f} ({os.path.getsize(local):,} bytes) OK")

    # verify
    stdin, stdout, stderr = ssh.exec_command(
        'python -c "import openpyxl; print(openpyxl.__version__)"'
    )
    print(f"  openpyxl: {stdout.read().decode().strip()}")

    stdin, stdout, stderr = ssh.exec_command(
        f'python -c "import sys; sys.path.insert(0, r\'{REMOTE_BASE}/tools/xlsx2dwg_net\'); '
        f'from xlsx2json import extract_to_json; print(\'xlsx2json OK\')"'
    )
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if out:
        print(f"  xlsx2json: {out}")
    else:
        print(f"  xlsx2json: FAILED {err}")

    sftp.close()
    ssh.close()
    print(f"  {host} done!\n")


if __name__ == "__main__":
    for h in WORKERS:
        try:
            deploy(h)
        except Exception as ex:
            print(f"  {h} FAILED: {ex}\n")
