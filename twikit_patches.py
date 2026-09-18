import twikit.user
import twikit.x_client_transaction

_original_user_init = twikit.user.User.__init__

def safe_user_init(self, client, data):
    if isinstance(data, dict):
        legacy = data.get('legacy')
        if not isinstance(legacy, dict):
            legacy = {}
            data['legacy'] = legacy
        entities = legacy.get('entities')
        if not isinstance(entities, dict):
            entities = {}
            legacy['entities'] = entities
        if not isinstance(entities.get('url'), dict):
            entities['url'] = {}
        if not isinstance(entities.get('description'), dict):
            entities['description'] = {}
    try:
        _original_user_init(self, client, data)
    except Exception:
        self._client = client
        self.id = str(data.get('rest_id', '')) if isinstance(data, dict) else ''
        self.name = data.get('core', {}).get('name', '') if isinstance(data, dict) else ''
        self.screen_name = data.get('core', {}).get('screen_name', '') if isinstance(data, dict) else ''
        self.profile_image_url = ''
        self.profile_banner_url = ''
        self.url = None
        self.location = ''
        self.description = ''
        self.description_urls = []
        self.urls = []
        self.pinned_tweet_ids = []
        self.is_blue_verified = False
        self.verified = False
        self.possibly_sensitive = False
        self.can_dm = False
        self.can_media_tag = False
        self.want_retweets = False
        self.default_profile = True
        self.default_profile_image = True
        self.has_custom_timelines = False
        self.followers_count = 0
        self.fast_followers_count = 0
        self.normal_followers_count = 0
        self.following_count = 0
        self.favourites_count = 0
        self.listed_count = 0
        self.media_count = 0
        self.statuses_count = 0
        self.is_translator = False
        self.translator_type = ''
        self.withheld_in_countries = []
        self.protected = False

twikit.user.User.__init__ = safe_user_init

async def _dummy_init(self, *args, **kwargs):
    self.key = '1234567890'
    self.key_bytes = [0] * 16

twikit.x_client_transaction.ClientTransaction.init = _dummy_init
twikit.x_client_transaction.ClientTransaction.generate_transaction_id = lambda *args, **kwargs: '1234567890'
