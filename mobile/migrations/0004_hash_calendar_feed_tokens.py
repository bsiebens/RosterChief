import hashlib

from django.db import migrations


def hash_existing_tokens(apps, schema_editor):
    """Store the SHA-256 of every existing feed token, so the URLs people have
    already subscribed to keep working once the raw token column is dropped."""
    CalendarFeedToken = apps.get_model("mobile", "CalendarFeedToken")
    for feed_token in CalendarFeedToken.objects.filter(token_hash__isnull=True).only("pk", "token"):
        feed_token.token_hash = hashlib.sha256(feed_token.token.encode()).hexdigest()
        feed_token.save(update_fields=["token_hash"])


class Migration(migrations.Migration):
    dependencies = [
        ("mobile", "0003_calendarfeedtoken_token_hash"),
    ]

    operations = [
        # The raw tokens can't be recovered from their hashes, so going back
        # leaves token_hash in place for the reverse of 0003 to drop.
        migrations.RunPython(hash_existing_tokens, migrations.RunPython.noop),
    ]
