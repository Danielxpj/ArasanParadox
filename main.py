# SPDX-License-Identifier: GPL-3.0-or-later
"""Arasan Paradox: native Linux interface inspired by Arasan's Windows GUI."""
import io
import json
import os
from pathlib import Path
import sys
import time
import chess
import chess.pgn
import chess.svg
import chess.engine
from PySide6.QtCore import Qt, QRectF, QTimer, QByteArray, QSettings
from PySide6.QtGui import QAction, QActionGroup, QColor, QPainter, QPen, QFont
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QComboBox, QMessageBox, QInputDialog,
    QDialog, QFormLayout, QDialogButtonBox, QSpinBox, QDoubleSpinBox, QCheckBox, QLineEdit,
    QFileDialog, QColorDialog, QSplitter, QHeaderView, QAbstractItemView, QToolButton)
from engine import Search, ENGINE
from model import Timeline

DEFAULTS = dict(mode='Fixed time per move', seconds=3.0, depth=12, minutes=5.0, increment=2.0,
    moves=40, threads=2, hash=128, elo=3450, limit_strength=False, book=True, variety=50,
    learning=False, tablebase='', beep=False, error_beep=False, idle=False, resign=False,
    autosave=True, light='#ffff99', dark='#427b3c', highlight='#edbd32', coordinates=True,
    legal=True, piece_set='Classic', board_size=640, auto_hash=False,
    store_games=True, game_path=str(Path.home()/'.local/state/arasan-paradox/games.pgn'))
MODES = ['Fixed time per move', 'Fixed depth', 'Time + depth limit', 'Incremental', 'Tournament']
STATE_DIR = Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))) / 'arasan-paradox'

class Board(QWidget):
    def __init__(self, game):
        super().__init__()
        self.game = game
        self.setMinimumSize(360,360)
        self.setMouseTracking(True)
        self.drag_source = None
        self.drag_origin = None
        self.drag_position = None
        self.drag_context = None
        self.dragging = False
        self.loaded_piece_set = None
        self.pieces = {p.symbol(): QSvgRenderer(QByteArray(chess.svg.piece(p).encode()))
                       for c in [False,True] for t in range(1,7) for p in [chess.Piece(t,c)]}
    def load_pieces(self):
        name=self.game.opts['piece_set']
        if name==self.loaded_piece_set: return
        self.loaded_piece_set=name
        for color in [False,True]:
            for kind in range(1,7):
                piece=chess.Piece(kind,color)
                path=Path(__file__).parent/'assets/pieces'/name.lower()/f'{"w" if color else "b"}{piece.symbol().upper()}.svg'
                svg=path.read_bytes() if path.exists() else chess.svg.piece(piece).encode()
                self.pieces[piece.symbol()]=QSvgRenderer(QByteArray(svg))
    def geometry_values(self):
        size = min(self.width(),self.height())-44
        return (self.width()-size)/2, (self.height()-size)/2, size/8
    def square_at(self,row,col):
        return chess.square(7-col if self.game.flipped else col, row if self.game.flipped else 7-row)
    def paintEvent(self,event):
        self.load_pieces()
        p=QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        ox,oy,s=self.geometry_values()
        g=self.game
        board=g.board
        last=board.peek() if board.move_stack else None
        targets={m.to_square for m in board.legal_moves if m.from_square==g.selected}
        for row in range(8):
            for col in range(8):
                square=self.square_at(row,col)
                rect=QRectF(ox+col*s,oy+row*s,s,s)
                p.fillRect(rect,QColor(g.opts['light'] if (row+col)%2==0 else g.opts['dark']))
                if last and square in (last.from_square,last.to_square):
                    shade=QColor(g.opts['highlight']); shade.setAlpha(150)
                    p.fillRect(rect,shade)
                    p.setPen(QPen(QColor('#8c5d00'),3))
                    p.drawRect(rect.adjusted(3,3,-3,-3))
                if board.is_check() and square==board.king(board.turn):
                    p.fillRect(rect,QColor(220,50,50,140))
                if square==g.selected:
                    p.setPen(QPen(QColor('#1683e8'),4)); p.drawRect(rect.adjusted(4,4,-4,-4))
                if g.hint_move and square in [g.hint_move.from_square,g.hint_move.to_square]:
                    p.setPen(QPen(QColor('#9426ce'),4)); p.drawRect(rect.adjusted(7,7,-7,-7))
                piece=board.piece_at(square)
                if piece and not (self.dragging and square==self.drag_source):
                    if g.opts['piece_set']=='Letters':
                        p.setFont(QFont('DejaVu Sans',int(s*.5),QFont.Bold))
                        p.setPen(QColor('white' if piece.color else 'black'))
                        p.drawText(rect,Qt.AlignCenter,piece.symbol().upper())
                    else:
                        self.pieces[piece.symbol()].render(p,rect.adjusted(s*.08,s*.08,-s*.08,-s*.08))
                if square in targets and g.opts['legal']:
                    p.setPen(Qt.NoPen); p.setBrush(QColor(20,60,120,145))
                    p.drawEllipse(rect.center(),s*.09,s*.09); p.setBrush(Qt.NoBrush)
        if g.opts['coordinates']:
            p.setPen(self.palette().text().color()); p.setFont(QFont('sans-serif',10))
            for i in range(8):
                p.drawText(QRectF(ox+i*s,oy+8*s,s,22),Qt.AlignCenter,'hgfedcba'[i] if g.flipped else 'abcdefgh'[i])
                p.drawText(QRectF(ox-22,oy+i*s,22,s),Qt.AlignCenter,str(i+1 if g.flipped else 8-i))
        if self.dragging and self.drag_position is not None:
            piece=board.piece_at(self.drag_source)
            if piece:
                rect=QRectF(self.drag_position.x()-s/2,self.drag_position.y()-s/2,s,s)
                if g.opts['piece_set']=='Letters':
                    p.setFont(QFont('DejaVu Sans',int(s*.5),QFont.Bold))
                    p.setPen(QColor('white' if piece.color else 'black'))
                    p.drawText(rect,Qt.AlignCenter,piece.symbol().upper())
                else:
                    self.pieces[piece.symbol()].render(p,rect.adjusted(s*.08,s*.08,-s*.08,-s*.08))
    def square_from_position(self,position):
        ox,oy,size=self.geometry_values()
        col,row=int((position.x()-ox)//size),int((position.y()-oy)//size)
        return self.square_at(row,col) if 0<=row<8 and 0<=col<8 else None
    def can_move(self):
        g=self.game
        return not (g.paused or g.terminal() or g.engine_controls(g.board.turn)
                    or (g.busy and g.search_kind!='idle'))
    def cancel_drag(self):
        self.drag_source=None; self.drag_origin=None; self.drag_position=None
        self.drag_context=None; self.dragging=False
        self.unsetCursor(); self.update()
    def try_move(self,source,target):
        g=self.game
        if not self.can_move() or source is None or target is None: return False
        moves=[m for m in g.board.legal_moves if m.from_square==source and m.to_square==target]
        if not moves: return False
        move=moves[0]
        if move.promotion:
            choice,ok=QInputDialog.getItem(self,'Promotion','Promote to:',['Queen','Rook','Bishop','Knight'],0,False)
            if not ok: return True
            move=chess.Move(source,target,{'Queen':5,'Rook':4,'Bishop':3,'Knight':2}[choice])
        if self.can_move(): g.play(move)
        return True
    def mousePressEvent(self,event):
        if event.button()!=Qt.LeftButton: return
        self.cancel_drag()
        g=self.game
        if not self.can_move():
            if g.paused: g.statusBar().showMessage('Press Space (Resume) before making a move.')
            return
        square=self.square_from_position(event.position())
        if square is None: return
        if self.try_move(g.selected,square): return
        piece=g.board.piece_at(square)
        g.selected=square if piece and piece.color==g.board.turn else None
        if g.selected is not None:
            self.drag_source=square
            self.drag_origin=event.position()
            self.drag_position=event.position()
            self.drag_context=(g.board.fen(),g.flipped)
        elif g.opts['error_beep']: QApplication.beep()
        self.update()
    def mouseMoveEvent(self,event):
        if self.drag_source is None: return
        if not (event.buttons() & Qt.LeftButton) or not self.can_move() or self.drag_context!=(self.game.board.fen(),self.game.flipped):
            self.cancel_drag(); return
        self.drag_position=event.position()
        if (event.position()-self.drag_origin).manhattanLength()>=QApplication.startDragDistance():
            self.dragging=True; self.setCursor(Qt.ClosedHandCursor)
        self.update()
    def mouseReleaseEvent(self,event):
        if event.button()!=Qt.LeftButton: return
        source=self.drag_source
        valid=self.drag_context==(self.game.board.fen(),self.game.flipped)
        dragged=self.dragging
        target=self.square_from_position(event.position())
        self.cancel_drag()
        if dragged and valid:
            if not self.try_move(source,target) and self.game.opts['error_beep']: QApplication.beep()
    def hideEvent(self,event):
        self.cancel_drag()
        super().hideEvent(event)

class Form(QDialog):
    def __init__(self,parent,title):
        super().__init__(parent); self.setWindowTitle(title)
        self.form=QFormLayout(self); self.fields={}
    def field(self,key,label,value,choices=None,minimum=0,maximum=10000):
        if choices:
            w=QComboBox(); w.addItems(choices); w.setCurrentText(str(value))
        elif isinstance(value,bool):
            w=QCheckBox(); w.setChecked(value)
        elif isinstance(value,int):
            w=QSpinBox(); w.setRange(minimum,maximum); w.setValue(value)
        elif isinstance(value,float):
            w=QDoubleSpinBox(); w.setRange(minimum,maximum); w.setDecimals(1); w.setValue(value)
        else: w=QLineEdit(str(value))
        self.fields[key]=w; self.form.addRow(label,w)
        return w
    def finish(self):
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        self.form.addRow(buttons)
    def values(self):
        out={}
        for k,w in self.fields.items():
            out[k]=w.isChecked() if isinstance(w,QCheckBox) else w.currentText() if isinstance(w,QComboBox) else w.value() if isinstance(w,(QSpinBox,QDoubleSpinBox)) else w.text()
        return out

class Game(QMainWindow):
    def __init__(self, restore=False):
        super().__init__()
        self.setWindowTitle('Arasan Paradox — Arasan 26.1')
        self.settings=QSettings('ArasanParadox','Chess')
        self.opts=DEFAULTS.copy()
        try: self.opts.update(json.loads(self.settings.value('options','{}')))
        except (ValueError,TypeError): pass
        self.timeline=Timeline(); self.collection=[]; self.collection_index=0
        self.filename=None; self.dirty=False
        self.flipped=False; self.selected=None; self.hint_move=None
        self.computer='Black'; self.paused=False
        self.busy=False; self.worker=None; self.workers=set(); self.generation=0; self.search_kind=None
        self.search_board=None; self.latest_info={}; self.resign_streak=0; self.recorded_game=None
        self.remaining={True:0.,False:0.}; self.last_tick=time.monotonic()
        self.actions={}
        self.build_ui()
        self.reset_clocks()
        self.resize(self.opts['board_size']+470,max(700,self.opts['board_size']+170))
        self.timer=QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(100)
        if restore: self.restore_session()
        self.refresh()
    @property
    def board(self): return self.timeline.board
    def timed(self): return self.opts['mode'] in ('Incremental','Tournament')
    def terminal(self):
        return self.board.is_game_over() or (self.timeline.index==self.timeline.end and self.timeline.result!='*')
    def engine_controls(self,color):
        return self.computer=='Both' or self.computer==('White' if color else 'Black')
    def action(self,key,text,callback,shortcut=None,checkable=False):
        a=QAction(text,self); a.setCheckable(checkable)
        if shortcut: a.setShortcut(shortcut)
        a.triggered.connect(callback); self.actions[key]=a
        return a
    def build_ui(self):
        definitions=[
            ('new','New game',self.new_game,'Ctrl+N'),('open','Open PGN…',self.open_pgn,'Ctrl+O'),
            ('save','Save PGN',self.save_pgn,'Ctrl+S'),('saveas','Save PGN as…',lambda:self.save_pgn(True),'Ctrl+Shift+S'),
            ('copypos','Copy position (FEN)',lambda:QApplication.clipboard().setText(self.board.fen()),None),
            ('copygame','Copy game (PGN)',lambda:QApplication.clipboard().setText(self.timeline.pgn()),None),
            ('paste','Paste position / game',self.paste,'Ctrl+V'),
            ('first','|◀ Beginning',lambda:self.seek(0),'Home'),('back','◀ Back',lambda:self.seek(self.timeline.index-1),'Ctrl+T'),
            ('next','Forward ▶',lambda:self.seek(self.timeline.index+1),'Ctrl+F'),('last','End ▶|',lambda:self.seek(self.timeline.end),'End'),
            ('go','Go',self.go,'Ctrl+G'),('stop','Move now',self.move_now,'Escape'),
            ('undo','Undo turn',self.undo_turn,'Ctrl+Z'),('hint','Hint',self.hint,'Ctrl+H'),
            ('analyze','Analyze position',self.analyze,'Ctrl+A'),('flip','Rotate board',self.flip,'Ctrl+R'),
            ('restart','Restart game',self.restart,None),('resign','Resign',self.resign,None),('draw','Claim draw',self.claim_draw,None),
            ('resetclock','Reset clocks',self.reset_clocks,None),('limits','Search limits…',self.search_limits,None),
            ('appearance','Appearance…',self.appearance,None),('colors','Board colors…',self.colors,None),
            ('prefs','Preferences…',self.preferences,None),('previousgame','Previous game',lambda:self.select_game(self.collection_index-1),None),
            ('nextgame','Next game',lambda:self.select_game(self.collection_index+1),None),('selectgame','Select game…',self.choose_game,None),
            ('help','Contents',self.help,None),('about','About Arasan Paradox',self.about,None),('exit','Exit',self.close,'Ctrl+Q')]
        for key,text,cb,sc in definitions: self.action(key,text,cb,sc)
        self.action('pause','Pause clocks / game',self.toggle_pause,'Space',True)
        menus={'File':['new','open','save','saveas','exit'], 'Edit':['copypos','copygame','paste'],
            'View':[], 'Browse':['previousgame','nextgame','selectgame'],
            'Game':['go','stop','back','next','first','last','undo','restart','hint','analyze','flip','resign','draw'],
            'Clock':['pause','resetclock'], 'Options':['limits','appearance','colors','prefs'], 'Help':['help','about']}
        self.menus={}
        for title,keys in menus.items():
            menu=self.menuBar().addMenu(title); self.menus[title]=menu
            for key in keys: menu.addAction(self.actions[key])
        side_menu=self.menus['Game'].addMenu('Computer plays')
        group=QActionGroup(self); self.side_actions={}
        for text in ['White','Black','Both','Neither']:
            a=QAction(text,self,checkable=True); a.setChecked(text==self.computer)
            a.triggered.connect(lambda checked=False,t=text:self.set_computer(t))
            group.addAction(a); side_menu.addAction(a); self.side_actions[text]=a
        self.side_actions['White'].setShortcut('Ctrl+W')
        self.toolbar=self.addToolBar('Main'); self.toolbar.setMovable(False)
        for key in ['new','open','save','first','back','go','stop','next','last','hint']:
            self.toolbar.addAction(self.actions[key])
        self.recent_menu=self.menus['File'].addMenu('Recent files')
        self.refresh_recent_files()
        self.menus['View'].addAction(self.toolbar.toggleViewAction())
        root=QWidget(); self.setCentralWidget(root); main=QVBoxLayout(root)
        split=QSplitter(); main.addWidget(split,1)
        self.view=Board(self); split.addWidget(self.view)
        self.panel=QWidget(); side=QVBoxLayout(self.panel); split.addWidget(self.panel)
        split.setStretchFactor(0,3); split.setStretchFactor(1,2)
        self.clocks={}
        clockrow=QHBoxLayout(); side.addLayout(clockrow)
        for color in [True,False]:
            label=QLabel(); label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(68); clockrow.addWidget(label); self.clocks[color]=label
        self.status=QLabel(); self.status.setWordWrap(True); side.addWidget(self.status)
        self.last=QLabel(); self.last.setWordWrap(True)
        self.last.setStyleSheet('background:#fff1b8;color:#443500;padding:10px;border:1px solid #ce9b22;font-weight:bold;')
        side.addWidget(self.last)
        self.history=QTableWidget(0,3); self.history.setHorizontalHeaderLabels(['Move','White','Black'])
        self.history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history.verticalHeader().hide(); self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setSelectionMode(QAbstractItemView.SingleSelection)
        self.history.cellClicked.connect(self.history_clicked)
        side.addWidget(self.history,1)
        self.comment=QLabel(); self.comment.setWordWrap(True); side.addWidget(self.comment)
        side.addWidget(QLabel('Computer plays'))
        self.color=QComboBox(); self.color.addItems(['White','Black','Both','Neither']); self.color.setCurrentText(self.computer)
        self.color.currentTextChanged.connect(self.set_computer); side.addWidget(self.color)
        self.limit_label=QLabel(); self.limit_label.setWordWrap(True); side.addWidget(self.limit_label)
        buttons=QHBoxLayout(); side.addLayout(buttons)
        for key in ['limits','prefs']:
            b=QPushButton(self.actions[key].text()); b.clicked.connect(self.actions[key].trigger); buttons.addWidget(b)
        nav=QHBoxLayout(); side.addLayout(nav)
        for key in ['undo','pause','restart']:
            b=QToolButton(); b.setDefaultAction(self.actions[key]); nav.addWidget(b)
        self.pv=QLabel('PV: —'); self.pv.setWordWrap(True); self.pv.setMinimumHeight(45)
        self.pv.setTextInteractionFlags(Qt.TextSelectableByMouse); main.addWidget(self.pv)
        self.stats=QLabel('Depth: —     Score: —     Nodes: —     Time: —'); main.addWidget(self.stats)
        for key,title,widget in [('pvview','PV display',self.pv),('movesview','Game moves',self.panel),('statusview','Status bar',self.statusBar())]:
            a=self.action(key,title,lambda checked,w=widget:w.setVisible(checked),checkable=True)
            a.setChecked(True); self.menus['View'].addAction(a)
    def refresh(self):
        self.view.cancel_drag()
        board=self.board
        last=board.peek() if board.move_stack else None
        if last:
            before=board.copy(); before.pop()
            self.last.setText(f'Last move · {"White" if before.turn else "Black"}: {chess.square_name(last.from_square)} → {chess.square_name(last.to_square)}   {before.san(last)}')
        else: self.last.setText('Last move: —  •  Both squares stay marked until the next move.')
        outcome=board.outcome()
        status=(f'Game over: {self.timeline.result}' if self.terminal() else
            f'Paused · move {self.timeline.index}/{self.timeline.end}' if self.paused else
            f'Arasan: {self.search_kind}…' if self.busy and self.search_kind!='idle' else
            f'{"White" if board.turn else "Black"} to move' + (' — check!' if board.is_check() else ''))
        if outcome: status+=f' · {outcome.termination.name.lower().replace("_"," ")}'
        self.status.setText(status)
        self.history.setRowCount(0)
        rows={}
        for index,node in enumerate(self.timeline.nodes[1:],1):
            before=node.parent.board(); num=before.fullmove_number
            if num not in rows:
                row=self.history.rowCount(); self.history.insertRow(row); rows[num]=row
                self.history.setItem(row,0,QTableWidgetItem(str(num)))
            row=rows[num]; col=1 if before.turn else 2
            item=QTableWidgetItem(before.san(node.move)); item.setData(Qt.UserRole,index)
            self.history.setItem(row,col,item)
            if index==self.timeline.index: self.history.setCurrentItem(item)
        if self.timeline.index==0: self.history.clearSelection()
        if self.history.currentItem(): self.history.scrollToItem(self.history.currentItem())
        self.comment.setText(self.timeline.nodes[self.timeline.index].comment)
        for key in ['first','back','undo']: self.actions[key].setEnabled(self.timeline.index>0)
        for key in ['next','last']: self.actions[key].setEnabled(self.timeline.index<self.timeline.end)
        self.actions['stop'].setEnabled(self.busy)
        self.actions['pause'].setChecked(self.paused)
        self.actions['pause'].setText('Resume' if self.paused else 'Pause')
        self.actions['previousgame'].setEnabled(self.collection_index>0)
        self.actions['nextgame'].setEnabled(self.collection_index+1<len(self.collection))
        self.actions['draw'].setEnabled(board.can_claim_draw() and not self.terminal())
        mode=self.opts['mode']
        desc={'Fixed time per move':f'{self.opts["seconds"]:g} seconds / move',
            'Fixed depth':f'Depth {self.opts["depth"]} plies',
            'Time + depth limit':f'{self.opts["seconds"]:g}s or depth {self.opts["depth"]} (first reached)',
            'Incremental':f'{self.opts["minutes"]:g} min + {self.opts["increment"]:g}s / move',
            'Tournament':f'{self.opts["moves"]} moves / {self.opts["minutes"]:g} minutes'}[mode]
        self.limit_label.setText(f'Search: {desc}')
        self.view.update(); self.update_clocks()
        self.setWindowTitle(f'Arasan Paradox{" *" if self.dirty else ""} — {Path(self.filename).name if self.filename else "Arasan 26.1"}')
    def history_clicked(self,row,col):
        item=self.history.item(row,col)
        if item and item.data(Qt.UserRole) is not None: self.seek(item.data(Qt.UserRole))
    def cancel_search(self):
        self.generation+=1
        if self.worker: self.worker.stop()
        self.worker=None; self.busy=False; self.search_kind=None
    def seek(self,index):
        self.tick(); self.cancel_search(); self.paused=True
        self.timeline.clocks[self.timeline.index]=self.remaining.copy()
        self.timeline.seek(index)
        self.remaining=self.timeline.clocks.get(self.timeline.index,self.initial_clocks()).copy()
        self.selected=None; self.hint_move=None; self.latest_info={}
        self.pv.setText('PV: —'); self.stats.setText('Depth: —     Score: —     Nodes: —     Time: —')
        self.refresh(); self.save_session()
    def undo_turn(self):
        index=max(0,self.timeline.index-1)
        if index>0 and self.engine_controls(self.timeline.nodes[index].board().turn): index-=1
        self.seek(index)
    def go(self):
        self.cancel_search(); self.paused=False; self.last_tick=time.monotonic()
        if self.terminal(): self.refresh(); return
        self.start_search('move')
    def resume(self):
        self.paused=False; self.last_tick=time.monotonic(); self.refresh(); self.think()
    def toggle_pause(self,checked=False):
        self.tick()
        if self.paused: self.resume()
        else: self.cancel_search(); self.paused=True; self.refresh()
    def set_computer(self,text):
        self.tick(); self.cancel_search(); self.computer=text
        self.color.blockSignals(True); self.color.setCurrentText(text); self.color.blockSignals(False)
        self.side_actions[text].setChecked(True)
        self.flipped=text=='White'; self.selected=None; self.resign_streak=0
        self.refresh(); self.save_session(); self.think()
    def think(self):
        if self.paused or self.busy or self.terminal(): return
        if self.engine_controls(self.board.turn): self.start_search('move')
        elif self.opts['idle']: self.start_search('idle')
    def limit(self,kind):
        if kind in ('analysis','idle'): return chess.engine.Limit()
        o=self.opts; mode=o['mode']
        if mode=='Fixed depth': return chess.engine.Limit(depth=o['depth'])
        if mode=='Time + depth limit': return chess.engine.Limit(time=o['seconds'],depth=o['depth'])
        if mode=='Fixed time per move': return chess.engine.Limit(time=o['seconds'])
        if kind=='hint': return chess.engine.Limit(time=min(o['seconds'],5))
        inc=o['increment'] if mode=='Incremental' else 0
        remaining_moves=None
        if mode=='Tournament':
            made=sum(n.parent.board().turn==self.board.turn for n in self.timeline.nodes[1:self.timeline.index+1])
            remaining_moves=o['moves']-made%o['moves']
        return chess.engine.Limit(white_clock=max(.01,self.remaining[True]),black_clock=max(.01,self.remaining[False]),
            white_inc=inc,black_inc=inc,remaining_moves=remaining_moves)
    def start_search(self,kind):
        if self.terminal() and kind=='move': return
        self.cancel_search(); self.search_kind=kind; self.busy=True; self.latest_info={}
        self.search_board=self.board.copy()
        o=self.opts
        memory_mb=os.sysconf('SC_PAGE_SIZE')*os.sysconf('SC_PHYS_PAGES')//(1024*1024)
        hash_mb=min(2048,max(16,memory_mb//16)) if o['auto_hash'] else o['hash']
        options={'Threads':o['threads'],'Hash':hash_mb,'OwnBook':o['book'] and kind=='move',
            'Book variety':o['variety'],'UCI_LimitStrength':o['limit_strength'],'UCI_Elo':o['elo'],
            'Position learning':o['learning'],'Use tablebases':bool(o['tablebase']),'SyzygyPath':o['tablebase'] or 'syzygy'}
        worker=Search(self.generation,self.board,self.limit(kind),options)
        self.worker=worker; self.workers.add(worker)
        worker.info.connect(self.engine_info); worker.result.connect(self.engine_result); worker.failed.connect(self.engine_error)
        worker.finished.connect(lambda w=worker:self.release_worker(w))
        worker.start(); self.refresh()
    def release_worker(self,worker):
        self.workers.discard(worker); worker.deleteLater()
        if self.worker is worker: self.worker=None
    def engine_info(self,token,info):
        if token!=self.generation: return
        self.latest_info.update(info); data=self.latest_info
        score=data.get('score'); scoretext='—'
        if score:
            value=score.white()
            scoretext=f'M{value.mate()}' if value.is_mate() else f'{value.score()/100:+.2f}'
        self.stats.setText(f'Depth: {data.get("depth","—")}   Score (White): {scoretext}   Nodes: {data.get("nodes",0):,}   N/s: {data.get("nps",0):,}   Time: {data.get("time",0):.1f}s')
        if 'pv' in data:
            b=self.search_board.copy(); moves=[]
            for m in data['pv']:
                if m not in b.legal_moves: break
                moves.append(b.san(m)); b.push(m)
            self.pv.setText('PV: '+' '.join(moves))
    def engine_result(self,token,move):
        if token!=self.generation: return
        kind=self.search_kind; self.busy=False; self.worker=None; self.search_kind=None
        if kind=='move' and move in self.board.legal_moves:
            score=self.latest_info.get('score')
            losing=score and score.pov(self.board.turn).score(mate_score=100000)<-900
            self.resign_streak=self.resign_streak+1 if losing else 0
            if self.opts['resign'] and self.resign_streak>=3:
                self.finish('0-1' if self.board.turn else '1-0','Arasan resigns.'); return
            self.play(move)
        elif kind=='hint' and move in self.board.legal_moves:
            self.hint_move=move; self.statusBar().showMessage('Hint: '+self.board.san(move),15000); self.refresh()
        else: self.refresh()
    def engine_error(self,token,error):
        if token!=self.generation: return
        self.busy=False; self.worker=None; self.paused=True; self.refresh()
        QMessageBox.warning(self,'Engine error',error)
    def move_now(self):
        if not self.worker: return
        if self.search_kind in ('analysis','idle'):
            self.cancel_search(); self.paused=True; self.refresh()
        else: self.worker.stop()
    def hint(self):
        if not self.terminal(): self.start_search('hint')
    def analyze(self):
        self.tick(); self.paused=True; self.start_search('analysis')
    def play(self,move):
        self.tick()
        if self.terminal(): return
        if self.timeline.index<self.timeline.end:
            if QMessageBox.question(self,'New continuation','Play a new continuation here? The previous line will be retained as a PGN variation.')!=QMessageBox.Yes: return
        self.cancel_search()
        mover=self.board.turn
        self.timeline.clocks[self.timeline.index]=self.remaining.copy()
        self.timeline.push(move)
        if self.opts['mode']=='Incremental': self.remaining[mover]+=self.opts['increment']
        elif self.opts['mode']=='Tournament':
            made=sum(n.parent.board().turn==mover for n in self.timeline.nodes[1:self.timeline.index+1])
            if made%self.opts['moves']==0: self.remaining[mover]+=self.opts['minutes']*60
        self.timeline.clocks[self.timeline.index]=self.remaining.copy()
        self.selected=None; self.hint_move=None; self.dirty=True; self.last_tick=time.monotonic()
        if self.opts['beep']: QApplication.beep()
        self.refresh(); self.save_session()
        if self.terminal(): self.record_completed_game()
        QTimer.singleShot(0,self.think)
    def initial_clocks(self): return {True:self.opts['minutes']*60 if self.timed() else 0.,False:self.opts['minutes']*60 if self.timed() else 0.}
    def reset_clocks(self):
        self.remaining=self.initial_clocks(); self.timeline.clocks={self.timeline.index:self.remaining.copy()}
        self.last_tick=time.monotonic(); self.update_clocks()
        if self.busy and self.search_kind=='move': self.cancel_search(); self.think()
    def tick(self):
        now=time.monotonic(); elapsed=now-self.last_tick; self.last_tick=now
        if not self.paused and not self.terminal():
            color=self.board.turn
            self.remaining[color]+= -elapsed if self.timed() else elapsed
            if self.timed() and self.remaining[color]<=0:
                self.remaining[color]=0
                result='1/2-1/2' if self.board.has_insufficient_material(not color) else ('0-1' if color else '1-0')
                self.finish(result,f'{"White" if color else "Black"} ran out of time.')
        self.update_clocks()
    def update_clocks(self):
        for color,label in self.clocks.items():
            seconds=max(0,int(self.remaining[color])); h,rem=divmod(seconds,3600); m,s=divmod(rem,60)
            label.setText(f'{"WHITE" if color else "BLACK"}\n{h:02}:{m:02}:{s:02}')
            active=self.board.turn==color and not self.paused and not self.terminal()
            label.setStyleSheet(f'font: bold 18px monospace; padding:8px; border:2px solid {"#c18c11" if active else "#aab0b8"}; background:{"#fff2be" if active else "#e9edf1"};color:#20242a;')
    def finish(self,result,message):
        self.cancel_search(); self.timeline.result=result; self.timeline.game.headers['Result']=result
        self.dirty=True; self.paused=True; self.refresh(); self.save_session(); self.record_completed_game(); self.statusBar().showMessage(message)
    def resign(self):
        if self.terminal(): return
        if QMessageBox.question(self,'Resign',f'{"White" if self.board.turn else "Black"} resigns?')==QMessageBox.Yes:
            self.finish('0-1' if self.board.turn else '1-0','Resigned.')
    def claim_draw(self):
        if self.board.can_claim_draw(): self.finish('1/2-1/2','Draw claimed.')
    def flip(self): self.view.cancel_drag(); self.flipped=not self.flipped; self.view.update()
    def confirm_discard(self):
        if not self.dirty: return True
        choice=QMessageBox.question(self,'Unsaved game','Save this game before continuing?',QMessageBox.Save|QMessageBox.Discard|QMessageBox.Cancel)
        if choice==QMessageBox.Cancel: return False
        if choice==QMessageBox.Save: return self.save_pgn()
        return True
    def new_game(self):
        if not self.confirm_discard(): return
        self.cancel_search(); self.timeline=Timeline(); self.filename=None; self.dirty=False
        self.collection=[]; self.collection_index=0; self.paused=False; self.selected=None; self.hint_move=None
        self.resign_streak=0; self.reset_clocks(); self.refresh(); self.save_session(); self.think()
    def restart(self):
        if QMessageBox.question(self,'Restart','Restart from the starting position? The existing game will remain available in the automatic recovery file until the next move.')!=QMessageBox.Yes: return
        self.cancel_search(); root=self.timeline.game.board()
        self.collection=[]; self.collection_index=0; self.filename=None
        game=chess.pgn.Game(); game.setup(root); self.timeline=Timeline(game)
        self.dirty=True; self.paused=False; self.selected=None; self.hint_move=None
        self.reset_clocks(); self.refresh(); self.think()
    def refresh_recent_files(self):
        self.recent_menu.clear()
        for filename in self.settings.value('recent_files',[]) or []:
            a=self.recent_menu.addAction(filename)
            a.triggered.connect(lambda checked=False,p=filename:self.open_pgn(p))
    def add_recent_file(self,filename):
        recent=list(self.settings.value('recent_files',[]) or [])
        recent=[filename]+[p for p in recent if p!=filename]
        self.settings.setValue('recent_files',recent[:10]); self.refresh_recent_files()
    def open_pgn(self,filename=None):
        if not filename:
            filename,_=QFileDialog.getOpenFileName(self,'Open PGN',str(Path.home()),'Chess games (*.pgn);;All files (*)')
        if not filename or not self.confirm_discard(): return
        try:
            games=[]
            with open(filename,encoding='utf-8-sig',errors='replace') as f:
                while (game:=chess.pgn.read_game(f)) is not None:
                    if game.errors: raise ValueError(str(game.errors[0]))
                    games.append(game)
            if not games: raise ValueError('No games found.')
            self.cancel_search(); self.collection=games; self.collection_index=0; self.filename=filename
            self.dirty=False; self.add_recent_file(filename); self.load_game(games[0])
        except Exception as exc: QMessageBox.warning(self,'Open PGN',str(exc))
    def load_game(self,game):
        self.cancel_search(); self.timeline=Timeline(game); self.paused=True; self.selected=None; self.hint_move=None
        self.reset_clocks(); self.refresh(); self.save_session()
    def select_game(self,index):
        if 0<=index<len(self.collection):
            self.collection_index=index; self.load_game(self.collection[index])
    def choose_game(self):
        if not self.collection:
            QMessageBox.information(self,'Select game','Open a PGN file containing games first.'); return
        labels=[f'{i+1}. {g.headers.get("White","?")} — {g.headers.get("Black","?")} ({g.headers.get("Result","*")})' for i,g in enumerate(self.collection)]
        item,ok=QInputDialog.getItem(self,'Select game','Game:',labels,self.collection_index,False)
        if ok: self.select_game(labels.index(item))
    def save_pgn(self,save_as=False):
        filename=self.filename
        if save_as or not filename:
            filename,_=QFileDialog.getSaveFileName(self,'Save PGN',filename or str(Path.home()/'arasan-game.pgn'),'Chess games (*.pgn)')
        if not filename: return False
        try:
            games=self.collection or [self.timeline.game]
            text='\n\n'.join(str(g.accept(chess.pgn.StringExporter(headers=True,variations=True,comments=True))) for g in games)+'\n'
            Path(filename).write_text(text,encoding='utf-8')
            self.filename=filename; self.dirty=False; self.add_recent_file(filename); self.refresh(); return True
        except OSError as exc: QMessageBox.warning(self,'Save PGN',str(exc)); return False
    def paste(self):
        text=QApplication.clipboard().text().strip()
        try:
            try:
                board=chess.Board(text)
                if not board.is_valid(): raise ValueError('Invalid chess position.')
                game=chess.pgn.Game(); game.setup(board)
            except ValueError:
                game=chess.pgn.read_game(io.StringIO(text))
                if not game or game.errors or not list(game.mainline_moves()): raise ValueError('Clipboard must contain a valid FEN position or PGN game.')
            if not self.confirm_discard(): return
            self.filename=None; self.collection=[]; self.collection_index=0; self.load_game(game); self.dirty=True; self.refresh()
        except Exception as exc: QMessageBox.warning(self,'Paste',str(exc))
    def store_options(self):
        self.settings.setValue('options',json.dumps(self.opts)); self.settings.sync(); self.refresh()
    def search_limits(self):
        d=Form(self,'Search Limits')
        d.field('mode','Type of search',self.opts['mode'],MODES)
        d.field('seconds','Seconds per move',self.opts['seconds'],minimum=.1,maximum=3600)
        d.field('depth','Fixed depth (plies)',self.opts['depth'],minimum=1,maximum=100)
        d.field('minutes','Minutes per side / period',self.opts['minutes'],minimum=.1,maximum=1440)
        d.field('increment','Increment (seconds)',self.opts['increment'],maximum=600)
        d.field('moves','Moves per tournament period',self.opts['moves'],minimum=1,maximum=1000)
        note=QLabel('Clock settings reset both clocks when applied.\nTime and depth are alternative limits unless “Time + depth” is selected.')
        note.setWordWrap(True); d.form.addRow(note)
        def update_fields():
            mode=d.fields['mode'].currentText()
            enabled={'seconds':mode in [MODES[0],MODES[2]],'depth':mode in [MODES[1],MODES[2]],
                'minutes':mode in MODES[3:],'increment':mode==MODES[3],'moves':mode==MODES[4]}
            for key,on in enabled.items(): d.fields[key].setEnabled(on)
        d.fields['mode'].currentTextChanged.connect(update_fields); update_fields(); d.finish()
        if d.exec():
            self.cancel_search(); self.opts.update(d.values()); self.reset_clocks(); self.store_options(); self.think()
    def preferences(self):
        d=Form(self,'Preferences')
        for key,label,minimum,maximum in [('threads','Processor cores',1,os.cpu_count() or 1),('hash','Hash table (MB)',4,8192),('elo','Playing strength (Elo)',1000,3450),('variety','Book variety',0,100)]:
            d.field(key,label,self.opts[key],minimum=minimum,maximum=maximum)
        for key,label in [('auto_hash','Automatically size hash table'),('limit_strength','Limit playing strength'),('book','Use opening book'),('learning','Position learning'),
                ('beep','Beep after move'),('error_beep','Beep on invalid selection'),('idle','Think when idle (background analysis)'),
                ('resign','Computer may resign'),('autosave','Store recovery game automatically'),('store_games','Save completed games to PGN archive')]:
            d.field(key,label,self.opts[key])
        d.field('tablebase','Syzygy tablebase folder',self.opts['tablebase'])
        d.field('game_path','Completed games pathname',self.opts['game_path'])
        note=QLabel('Automatic resignation: worse than −9 pawns for 3 engine moves.\nRecovery game: '+str(STATE_DIR/'recovery.pgn'))
        note.setWordWrap(True); d.form.addRow(note); d.finish()
        if d.exec(): self.cancel_search(); self.opts.update(d.values()); self.store_options(); self.think()
    def appearance(self):
        d=Form(self,'Appearance')
        d.field('piece_set','Piece set',self.opts['piece_set'],['Classic','Celtic','Spatial','Letters'])
        d.field('board_size','Preferred board size (pixels)',self.opts['board_size'],minimum=360,maximum=1400)
        d.field('coordinates','Show coordinates',self.opts['coordinates'])
        d.field('legal','Show legal destination dots',self.opts['legal']); d.finish()
        if d.exec():
            self.opts.update(d.values()); self.store_options()
            self.resize(self.opts['board_size']+470,self.opts['board_size']+170)
    def colors(self):
        d=QDialog(self); d.setWindowTitle('Board Colors'); layout=QFormLayout(d); colors={}
        for key,label in [('light','Light squares'),('dark','Dark squares'),('highlight','Last-move highlight')]:
            colors[key]=self.opts[key]; b=QPushButton(colors[key]); b.setStyleSheet(f'background:{colors[key]};color:black;')
            def choose(checked=False,k=key,button=b):
                color=QColorDialog.getColor(QColor(colors[k]),d)
                if color.isValid(): colors[k]=color.name(); button.setText(color.name()); button.setStyleSheet(f'background:{color.name()};color:black;')
            b.clicked.connect(choose); layout.addRow(label,b)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); buttons.accepted.connect(d.accept); buttons.rejected.connect(d.reject); layout.addRow(buttons)
        if d.exec(): self.opts.update(colors); self.store_options()
    def record_completed_game(self):
        if not self.opts['store_games'] or self.timeline.result=='*': return
        text=self.timeline.pgn()
        if self.recorded_game==text: return
        try:
            path=Path(self.opts['game_path']).expanduser()
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('a',encoding='utf-8') as f: f.write(text+'\n\n')
            self.recorded_game=text
        except OSError as exc: self.statusBar().showMessage('Game archive failed: '+str(exc))
    def save_session(self):
        if not self.opts['autosave']: return
        try:
            STATE_DIR.mkdir(parents=True,exist_ok=True)
            target=STATE_DIR/'recovery.pgn'; temp=target.with_suffix('.tmp')
            temp.write_text(self.timeline.pgn(),encoding='utf-8'); temp.replace(target)
            data=dict(index=self.timeline.index,computer=self.computer,flipped=self.flipped,remaining=self.remaining)
            target=STATE_DIR/'session.json'; temp=target.with_suffix('.tmp')
            temp.write_text(json.dumps(data)); temp.replace(target)
        except OSError as exc: self.statusBar().showMessage('Recovery save failed: '+str(exc))
    def restore_session(self):
        if not self.opts['autosave']: return
        try:
            pgn=(STATE_DIR/'recovery.pgn').read_text(); data=json.loads((STATE_DIR/'session.json').read_text())
            game=chess.pgn.read_game(io.StringIO(pgn))
            if game is None or game.errors: return
            self.timeline=Timeline(game); self.timeline.seek(data['index'])
            self.computer=data.get('computer','Black'); self.flipped=data.get('flipped',False)
            self.color.blockSignals(True); self.color.setCurrentText(self.computer); self.color.blockSignals(False)
            self.side_actions[self.computer].setChecked(True)
            self.remaining={True:data['remaining']['true'],False:data['remaining']['false']}
            self.paused=True; self.dirty=bool(self.timeline.end)
            self.statusBar().showMessage('Recovered last session. Press Space to resume, or New for a fresh game.')
        except (OSError,ValueError,KeyError,TypeError): pass
    def help(self):
        QMessageBox.information(self,'Arasan Paradox Help',
            'Drag a piece to its destination, or click the piece and then the destination. Gold marks the last move.\n\n'
            'Options → Search Limits: fixed seconds, depth in plies, both limits, increment, or tournament clocks.\n'
            'Computer plays selects White, Black, Both, or Neither. Switching sides applies immediately.\n'
            'Back/Forward and Beginning/End browse without deleting moves. Browsing pauses play. Space resumes the chosen sides; Go asks Arasan to play the current side.\n'
            'Undo turn rewinds to your previous turn. New continuations preserve the old line as a PGN variation.\n'
            'Move now stops a move search and plays its best result; during analysis it stops without moving.\n'
            'Hint marks a suggested move in purple. Analyze shows live depth, score, nodes and PV without moving.\n'
            'Open/Save support PGN collections, comments and variations. Browse selects a game. Copy/Paste support PGN and FEN.\n'
            'Recovery is saved automatically and restored paused. Original reference screenshots and feature notes are in the project folder.')
    def about(self):
        QMessageBox.about(self,'About Arasan Paradox','Arasan Paradox — native Linux chess GUI\nPowered by Arasan 26.1 by Jon Dart.\nAn independent Qt interface inspired by the original Windows menus.\nBuilt with PySide6 and python-chess.\nhttps://arasanchess.org')
    def closeEvent(self,event):
        self.tick(); self.save_session(); self.cancel_search()
        # Search.stop() sends UCI stop, so ordinary shutdown is immediate.
        for worker in list(self.workers):
            worker.stop()
            if not worker.wait(12000):
                event.ignore(); self.statusBar().showMessage('Waiting for the engine to exit…'); return
        self.settings.setValue('options',json.dumps(self.opts)); event.accept()

def main():
    app=QApplication(sys.argv); app.setApplicationName('Arasan Paradox'); app.setStyle('Fusion')
    # A light desktop palette matches the reference and keeps piece outlines legible.
    from PySide6.QtGui import QPalette
    palette=QPalette()
    for role,color in [(QPalette.Window,'#efefef'),(QPalette.WindowText,'#20242a'),(QPalette.Base,'#ffffff'),
        (QPalette.AlternateBase,'#f1f3f5'),(QPalette.Text,'#20242a'),(QPalette.Button,'#e8e8e8'),
        (QPalette.ButtonText,'#20242a'),(QPalette.Highlight,'#cadcf3'),(QPalette.HighlightedText,'#101820')]: palette.setColor(role,QColor(color))
    app.setPalette(palette)
    app.setStyleSheet('QToolBar {spacing:4px;padding:4px;} QToolButton {padding:5px;} QPushButton {padding:5px;} QTableWidget {font-size:15px;}')
    window=Game(restore=True); window.show(); sys.exit(app.exec())

if __name__=='__main__': main()
