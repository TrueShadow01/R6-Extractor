"""Desktop entry point with subprocess worker dispatch"""

import sys
import traceback

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        if len(sys.argv) < 3:
            raise ValueError("Missing worker name")

        worker = sys.argv[2]
        arguments = sys.argv[3:]

        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace", write_through=True)

        if worker == "cli":
            from src.cli import main as cli_main

            sys.argv = ["main.py", *arguments]
            return cli_main()

        if worker == "install":
            from blender.install_addon import install

            if len(arguments) != 1:
                raise ValueError("The install worker requires a Blender path")
            install(arguments[0])
            return 0

        if worker == "preview":
            from desktop.preview_cache import prepare_preview

            if len(arguments) != 2:
                raise ValueError("Preview worker requires a game folder and operator UID")
            prepare_preview(arguments[0], int(arguments[1], 16))
            return 0

        if worker == "audit":
            from devtools.audit.runner import main as audit_main

            return audit_main(arguments)

        if worker == "audit-gallery":
            from devtools.audit.gallery import main as gallery_main

            return gallery_main(arguments)

        raise ValueError(f"Unknown worker: {worker}")

    from desktop.gui import main as gui_main

    return gui_main()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)