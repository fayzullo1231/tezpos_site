"""WhiteNoise: manifestda yo‘q static fayl 500 bermasin."""
from whitenoise.storage import CompressedManifestStaticFilesStorage


class SoftManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    # Django 4.2+ — yo‘q entry ValueError o‘rniga oddiy yo‘l qaytaradi
    manifest_strict = False
