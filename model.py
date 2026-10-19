# SPDX-License-Identifier: GPL-3.0-or-later
"""Game timeline. Browsing never destroys future moves."""
import chess
import chess.pgn

class Timeline:
    def __init__(self, game=None):
        self.load(game or chess.pgn.Game())

    def load(self, game):
        self.game = game
        self.nodes = [game] + list(game.mainline())
        self.index = 0
        self.clocks = {}
        self.result = game.headers.get('Result', '*')

    @property
    def board(self): return self.nodes[self.index].board()

    @property
    def end(self): return len(self.nodes)-1

    def seek(self, index):
        self.index = max(0, min(index, self.end))

    def push(self, move):
        board = self.board
        if move not in board.legal_moves: raise ValueError('Illegal move')
        node = self.nodes[self.index]
        # Retain the old continuation as a PGN variation when branching.
        existing = next((n for n in node.variations if n.move == move), None)
        child = existing or node.add_variation(move)
        node.promote_to_main(child)
        self.nodes = [self.game] + list(self.game.mainline())
        self.index += 1
        self.clocks = {k:v for k,v in self.clocks.items() if k < self.index}
        outcome = self.board.outcome()
        self.result = outcome.result() if outcome else '*'
        self.game.headers['Result'] = self.result

    def pgn(self):
        return str(self.game.accept(chess.pgn.StringExporter(headers=True, variations=True, comments=True)))
