"""PerCell4 development entry point.

Run with: python main.py
Or after install: percell4-gui
"""

if __name__ == "__main__":
    # Import inside the guard so multiprocessing 'spawn' workers (which
    # re-import this module) don't pull in the GUI/Qt stack — keeps the
    # parallel-decode worker startup fast.
    from percell4.app import main

    main()
