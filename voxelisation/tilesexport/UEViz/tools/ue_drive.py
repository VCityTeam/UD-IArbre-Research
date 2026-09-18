"""Drive the running UE editor over Python remote execution.

Usage: python ue_drive.py <command-file.py>
Sends the file's contents to the first editor node found and prints output.
"""
import os
import sys
# The engine's bundled remote_execution module - adjust to your UE install path,
# or set UE_PY_PATH in the environment to override the default below.
UE_PY = os.environ.get("UE_PY_PATH", "")
if not UE_PY:
    sys.exit("Set UE_PY_PATH to your Unreal Engine install's "
             "Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python directory.")
sys.path.insert(0, UE_PY)
import remote_execution as remote

def main() -> None:
    cmd_path = sys.argv[1]
    with open(cmd_path, encoding="utf-8") as f:
        code = f.read()

    rex = remote.RemoteExecution()
    rex.start()
    try:
        import time
        deadline = time.time() + 15
        while time.time() < deadline and not rex.remote_nodes:
            time.sleep(0.5)
        nodes = rex.remote_nodes
        if not nodes:
            print("NO_EDITOR_FOUND")
            return
        rex.open_command_connection(nodes[0]["node_id"])
        result = rex.run_command(code, exec_mode=remote.MODE_EXEC_FILE)
        print("SUCCESS:", result.get("success"))
        for entry in result.get("output", []):
            print(f"[{entry['type']}] {entry['output']}", end="")
    finally:
        rex.stop()

if __name__ == "__main__":
    main()
