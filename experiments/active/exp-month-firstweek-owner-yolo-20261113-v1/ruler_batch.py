"""Apply the identical blinded review and frozen detector to extra ruler dates.
The additional data ledger has its own outputs so the original run is auditable.
"""
import sys
from pathlib import Path
import windows

def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ('blind','detect','day','compare'):
        raise SystemExit('choose blind, detect, day or compare')
    old_data=windows.DATA
    windows.HERE=windows.HERE/'ruler'
    windows.DATA=old_data/'ruler'
    windows.OUT=windows.OUT/'ruler'
    windows.DATA.mkdir(exist_ok=True)
    tapes=windows.DATA/'tapes'
    try:
        tapes.symlink_to('../tapes', target_is_directory=True)
    except FileExistsError:
        pass
    assert tapes.resolve()==(old_data/'tapes').resolve()
    from importlib import import_module
    import_module(sys.argv[1]).main()

if __name__=='__main__':main()
