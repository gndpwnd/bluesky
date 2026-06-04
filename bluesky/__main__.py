import sys
import traceback
import bluesky as bs
from bluesky import cmdargs


def main():
    """
        Start BlueSky: This is the main entrypoint for BlueSky.
        Depending on settings and arguments passed it can start in different
        modes. The central part of BlueSky consists of a server managing all
        simulations, normally together with a gui. The different modes for this
        are:
        - server-gui: Start gui and simulation server
        - server-headless: start server without gui
        - client: start gui only, which can connect to an already running server

        A BlueSky server can start one or more simulation processes, which run
        the actual simulations. These simulations can also be started completely
        separate from all other BlueSky functionality, in the detached mode.
        This is useful when calling bluesky from within another python
        script/program. The corresponding modes are:
        - sim: The normal simulation process started by a BlueSky server
        - sim-detached: An isolated simulation node, without networking
    """
    # When importerror gives different name than (pip) install needs,
    # also advise latest version
    missingmodules = {"OpenGL": "pyopengl"}

    # Catch import errors
    try:
        # Parse command-line arguments
        args = cmdargs.parse()
        # Initialize bluesky modules. Pass command-line arguments parsed by cmdargs
        bs.init(**args)

        # Only start a simulation node if called with --sim or --detached
        if bs.mode == 'sim':
            bs.net.connect()
            bs.sim.run()
        else:
            # Only print start message in the non-sim cases to avoid printing
            # this for every started node
            print("   *****   BlueSky Open ATM simulator *****")
            print("Distributed under GNU General Public License v3")

        # Start server if server/gui or server-headless is started here
        if bs.mode == 'server':
            if bs.gui is None:
                bs.server.run()
            else:
                bs.server.start()

        # Start gui if client or main server/gui combination is started here
        if bs.gui == 'qtgl':
            from bluesky.ui import qtgl
            qtgl.start(hostname=args.get('hostname'))

        elif bs.gui == 'console':
            from bluesky.ui import console
            console.start(hostname=args.get('hostname'))

    # Give info on missing module
    except ImportError as error:
        modulename = missingmodules.get(error.name) or error.name
        if modulename is None or 'bluesky' in modulename:
            # BluePlan-obs (Audit 6 EP3): emit a structured startup_failed
            # marker even when this branch re-raises, so the runner sees the
            # import failure before the process exits.
            print(f'[BLUESKY_FATAL] phase=startup kind=ImportError module={modulename} msg={error}')
            traceback.print_exc()
            raise error
        print("Bluesky needs", modulename)
        print("Run setup-python.bat (Windows) or check requirements.txt (other systems)")
        print("Install using e.g. pip install", modulename)

    # BluePlan-obs (Audit 6 EP3): broaden the net for non-import startup
    # failures. The point is observability — log a structured marker, then
    # re-raise so the process exits with a non-zero status (NOT silent
    # swallow). Previously these blew up with no parseable signature.
    except Exception as startup_exc:
        try:
            print(f'[BLUESKY_FATAL] phase=startup kind={type(startup_exc).__name__} msg={startup_exc}')
            traceback.print_exc()
        except Exception:
            # Defensive: even structured logging can fail if stdout is gone.
            pass
        raise

    finally:
        # Shut down logging manager
        from bluesky.logging_manager import shutdown_logging
        shutdown_logging()

    print('BlueSky normal end.')

if __name__ == '__main__':
    sys.exit(main())
