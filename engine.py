# SPDX-License-Identifier: GPL-3.0-or-later
"""Cancellable UCI searches; all engine work stays off the Qt UI thread."""
import os
import shutil
import threading
import chess
import chess.engine
from PySide6.QtCore import QThread, Signal

ENGINE = os.environ.get('ARASAN_ENGINE') or shutil.which('arasan') or os.path.expanduser('~/.local/bin/arasan')

class Search(QThread):
    info = Signal(int, object)
    result = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, token, board, limit, options):
        super().__init__()
        self.token, self.board, self.limit, self.options = token, board.copy(), limit, options
        self.cancelled = threading.Event()
        self.analysis = None

    def stop(self):
        self.cancelled.set()
        if self.analysis is not None:
            self.analysis.stop()

    def run(self):
        try:
            with chess.engine.SimpleEngine.popen_uci(ENGINE, timeout=10) as engine:
                engine.configure({k: v for k, v in self.options.items() if k in engine.options})
                if self.cancelled.is_set(): return
                with engine.analysis(self.board, self.limit, info=chess.engine.INFO_ALL) as analysis:
                    self.analysis = analysis
                    if self.cancelled.is_set(): analysis.stop()
                    for info in analysis:
                        self.info.emit(self.token, info)
                    result = analysis.wait()
                    self.result.emit(self.token, result.move)
        except Exception as exc:
            if not self.cancelled.is_set(): self.failed.emit(self.token, str(exc))
        finally:
            self.analysis = None
