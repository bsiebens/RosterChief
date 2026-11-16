"""Live game receivers. Registered from ``EventsConfig.ready``.

* a game's timeline changing (events.services.game_timeline.store sends ``game_timeline_changed``) records the "moments"
  in it -- goals, penalties, the result -- as GameMoment rows and pushes the notifiable ones to the game's followers
  (events.services.game_moments.record_moments -> events.services.game_push).
"""

from events.services.game_moments import game_timeline_changed, record_moments

game_timeline_changed.connect(record_moments, dispatch_uid="events.record_game_moments")
