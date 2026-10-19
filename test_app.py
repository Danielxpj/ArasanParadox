# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with: QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest -v"""
import os
import tempfile
# Isolate tests from the user's settings, recovery game, and completed games.
TEMP=tempfile.TemporaryDirectory()
os.environ['XDG_CONFIG_HOME']=TEMP.name+'/config'
os.environ['XDG_STATE_HOME']=TEMP.name+'/state'
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import io
import time
import unittest
from unittest.mock import patch
import chess
import chess.pgn
from main import QApplication, Game, DEFAULTS, QMessageBox, QSettings, QTimer
from model import Timeline

APP=QApplication.instance() or QApplication([])

def pump_until(predicate,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        APP.processEvents()
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Operation timed out')

class TimelineTests(unittest.TestCase):
    def test_navigation_and_branch_pgn(self):
        t=Timeline()
        for uci in ['e2e4','e7e5','g1f3']: t.push(chess.Move.from_uci(uci))
        final=t.board.fen(); t.seek(0); self.assertEqual(t.end,3)
        t.seek(3); self.assertEqual(t.board.fen(),final)
        t.seek(1); t.push(chess.Move.from_uci('c7c5'))
        exported=t.pgn(); self.assertIn('e5',exported); self.assertIn('c5',exported)
        loaded=chess.pgn.read_game(io.StringIO(exported))
        self.assertFalse(loaded.errors)
        self.assertEqual(str(list(loaded.mainline_moves())[-1]),'c7c5')
    def test_special_moves(self):
        for fen,uci in [('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1','e1g1'),
            ('4k3/8/8/3pP3/8/8/8/4K3 w - d6 0 1','e5d6'),
            ('4k3/P7/8/8/8/8/8/4K3 w - - 0 1','a7a8n')]:
            game=chess.pgn.Game(); game.setup(chess.Board(fen)); t=Timeline(game)
            t.push(chess.Move.from_uci(uci)); after=t.board.fen(); t.seek(0); t.seek(1)
            self.assertEqual(t.board.fen(),after)
    def test_comments_and_variations_survive(self):
        game=chess.pgn.read_game(io.StringIO('[Result "*"]\n\n1. e4 {hello} e5 (1... c5) 2. Nf3 *'))
        t=Timeline(game); self.assertIn('{ hello }',t.pgn()); self.assertIn('c5',t.pgn())

class AppTests(unittest.TestCase):
    def setUp(self):
        QSettings('ArasanParadox','Chess').clear()
        self.g=Game(); self.g.opts.update(DEFAULTS)
        self.g.opts.update(autosave=False,store_games=False,book=False,depth=4,seconds=.15)
        self.g.computer='Neither'; self.g.paused=True
    def tearDown(self):
        self.g.cancel_search(); self.g.close(); APP.processEvents()
    def test_depth_and_time_limits(self):
        g=self.g; g.opts['mode']='Fixed depth'
        self.assertEqual(g.limit('move').depth,4)
        self.assertIsNone(g.limit('move').time)
        g.start_search('move'); pump_until(lambda:not g.busy)
        self.assertEqual(g.timeline.end,1); self.assertGreaterEqual(g.latest_info['depth'],4)
        g.opts['mode']='Time + depth limit'
        self.assertEqual(g.limit('move').depth,4); self.assertEqual(g.limit('move').time,.15)
    def test_timed_search_and_hint(self):
        g=self.g; g.opts['mode']='Fixed time per move'
        g.start_search('hint'); pump_until(lambda:not g.busy)
        self.assertEqual(g.timeline.end,0); self.assertIn(g.hint_move,g.board.legal_moves)
        self.assertIn('time',g.latest_info)
    def test_cancellation_rejects_stale_move(self):
        g=self.g; g.start_search('analysis'); token=g.generation
        pump_until(lambda:bool(g.latest_info))
        g.seek(0); g.engine_result(token,chess.Move.from_uci('e2e4'))
        pump_until(lambda:not g.workers)
        self.assertEqual(g.timeline.end,0); self.assertFalse(g.busy)
    def test_move_now(self):
        g=self.g; g.opts['seconds']=60; g.start_search('move')
        pump_until(lambda:g.latest_info.get('depth',0)>=6)
        g.move_now(); pump_until(lambda:not g.busy)
        self.assertEqual(g.timeline.end,1)
    def test_computer_white_and_black(self):
        g=self.g; g.opts['mode']='Fixed depth'; g.paused=False
        g.set_computer('White'); pump_until(lambda:g.timeline.end==1)
        self.assertFalse(g.board.turn); self.assertFalse(g.flipped)
        g.set_computer('Black'); pump_until(lambda:g.timeline.end==2)
        self.assertTrue(g.board.turn); self.assertFalse(g.flipped)
    def test_computer_selection_preserves_manual_orientation(self):
        g=self.g
        g.show(); APP.processEvents()
        self.assertTrue(g.rotate_button.isVisible())
        self.assertEqual(g.rotate_button.accessibleName(),'Rotate board')
        for flipped in [True,False]:
            g.rotate_button.click()
            self.assertEqual(g.flipped,flipped)
            for side in ['White','Black','Both','Neither']:
                g.color.setCurrentText(side)
                self.assertEqual(g.flipped,flipped)
                g.side_actions[side].trigger()
                self.assertEqual(g.flipped,flipped)
    def test_increment_tournament_and_timeout(self):
        g=self.g; g.opts.update(mode='Incremental',minutes=1.,increment=2.)
        g.reset_clocks(); g.play(chess.Move.from_uci('e2e4'))
        self.assertEqual(g.remaining[True],62.)
        g.opts.update(mode='Tournament',moves=1,minutes=1.)
        g.reset_clocks(); self.assertEqual(g.limit('move').remaining_moves,1)
        g.play(chess.Move.from_uci('e7e5')); self.assertEqual(g.remaining[False],120.)
        g.paused=False; g.remaining[True]=.01; g.last_tick=time.monotonic()-1; g.tick()
        self.assertEqual(g.timeline.result,'0-1'); self.assertTrue(g.paused)
    def test_navigation_highlight_and_clocks(self):
        g=self.g
        for uci in ['e2e4','e7e5']: g.play(chess.Move.from_uci(uci))
        self.assertIn('e7 → e5',g.last.text())
        g.seek(1); self.assertIn('e2 → e4',g.last.text()); self.assertEqual(g.timeline.end,2)
        label=g.last.text(); g.flip(); self.assertEqual(label,g.last.text())
        g.seek(2); self.assertIn('e7 → e5',g.last.text())
        g.seek(0); self.assertIn('Last move: —',g.last.text())
    def test_pgn_collection_save(self):
        path=TEMP.name+'/collection.pgn'
        with open(path,'w') as f: f.write('1. e4 e5 *\n\n1. d4 d5 *\n')
        g=self.g; g.open_pgn(path); self.assertEqual(len(g.collection),2)
        g.select_game(1); g.seek(2); self.assertEqual(g.board.peek().uci(),'d7d5')
        g.play(chess.Move.from_uci('c2c4')); self.assertTrue(g.save_pgn())
        with open(path) as f:
            first=chess.pgn.read_game(f); second=chess.pgn.read_game(f)
        self.assertEqual(len(list(first.mainline_moves())),2)
        self.assertEqual(len(list(second.mainline_moves())),3)
    def test_board_click_input(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QPoint, Qt
        g=self.g; g.show(); g.paused=False; APP.processEvents()
        ox,oy,size=g.view.geometry_values()
        for row in [6,4]:
            QTest.mouseClick(g.view,Qt.LeftButton,pos=QPoint(int(ox+4.5*size),int(oy+(row+.5)*size)))
        self.assertEqual(g.board.peek().uci(),'e2e4')
        self.assertIn('e2 → e4',g.last.text())
    def drag_piece(self,source,target,assert_drag=True):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QPoint, Qt
        g=self.g; g.show(); APP.processEvents()
        ox,oy,size=g.view.geometry_values()
        def point(square):
            col=chess.square_file(square); row=7-chess.square_rank(square)
            if g.flipped: col,row=7-col,7-row
            return QPoint(int(ox+(col+.5)*size),int(oy+(row+.5)*size))
        start=point(chess.parse_square(source))
        end=point(chess.parse_square(target)) if target else QPoint(0,0)
        QTest.mousePress(g.view,Qt.LeftButton,pos=start)
        QTest.mouseMove(g.view,end,delay=10)
        if assert_drag: self.assertTrue(g.view.dragging)
        QTest.mouseRelease(g.view,Qt.LeftButton,pos=end)
        APP.processEvents()
    def test_drag_legal_move_and_flipped_board(self):
        g=self.g; g.paused=False
        self.drag_piece('e2','e4')
        self.assertEqual(g.board.peek().uci(),'e2e4')
        self.assertIn('e2 → e4',g.last.text())
        g.flip(); self.drag_piece('d7','d5')
        self.assertEqual(g.board.peek().uci(),'d7d5')
        self.assertFalse(g.view.dragging)
    def test_drag_illegal_and_outside_drops(self):
        g=self.g; g.paused=False; initial=g.board.fen()
        self.drag_piece('e2','e5'); self.assertEqual(g.board.fen(),initial)
        self.drag_piece('e2',None); self.assertEqual(g.board.fen(),initial)
        self.assertIsNone(g.view.drag_source)
    def test_drag_capture_and_promotion(self):
        g=self.g; g.paused=False
        g.play(chess.Move.from_uci('e2e4')); g.play(chess.Move.from_uci('d7d5'))
        self.drag_piece('e4','d5'); self.assertEqual(g.board.piece_at(chess.D5),chess.Piece(chess.PAWN,True))
        game=chess.pgn.Game(); game.setup(chess.Board('4k3/P7/8/8/8/8/8/4K3 w - - 0 1'))
        g.load_game(game); g.paused=False
        with patch('main.QInputDialog.getItem',return_value=('Knight',True)):
            self.drag_piece('a7','a8')
        self.assertEqual(g.board.piece_at(chess.A8),chess.Piece(chess.KNIGHT,True))
    def test_drag_blocked_when_paused_or_engine_turn(self):
        g=self.g; initial=g.board.fen()
        self.drag_piece('e2','e4',assert_drag=False)
        self.assertEqual(g.board.fen(),initial)
        g.computer='White'; g.paused=False
        self.drag_piece('e2','e4',assert_drag=False)
        self.assertEqual(g.board.fen(),initial)
    def test_recovery_preserves_position(self):
        g=self.g; g.opts['autosave']=True
        for uci in ['d2d4','d7d5']: g.play(chess.Move.from_uci(uci))
        g.seek(1); fen=g.board.fen(); g.save_session()
        restored=Game(restore=True)
        try:
            self.assertEqual(restored.board.fen(),fen)
            self.assertEqual(restored.timeline.end,2)
            self.assertTrue(restored.paused)
        finally: restored.close()
    def test_piece_sets_render(self):
        g=self.g; g.show(); APP.processEvents()
        for name in ['Classic','Celtic','Spatial','Letters']:
            g.opts['piece_set']=name; g.view.repaint(); APP.processEvents()
            self.assertFalse(g.grab().isNull())

if __name__=='__main__': unittest.main()
