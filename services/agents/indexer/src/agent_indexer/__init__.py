"""
Indexer agent – runs as a Celery task.
Loads → chunks → embeds → upserts into pgvector + metadata.
"""
