"""Competition data sources: one class per competition (``club.models.Competition.module`` is its dotted path).

Each class refreshes one existing game's score/live state (``update_game_information``) and, optionally, offers the game's
timeline (``fetch_timeline``). Creating games from a fixture list is ``events.services.*_import``.
"""
