import time

from motor.motor_asyncio import AsyncIOMotorClient

from settings import MONGODB_URI


class MetaModel(type):

    def __call__(cls, *args, **kwargs):
        instance = super(MetaModel, cls).__call__(*args, **kwargs)
        instance.db = AsyncIOMotorClient(MONGODB_URI)
        return instance


class Model(metaclass=MetaModel):

    collection = ''

    async def find_one(self, condition):
        db = self.db.evernoterobot
        entry = await db[self.collection].find_one(condition)
        return entry


class StartSession(Model):

    collection = 'start_sessions'

    def __init__(self, user_id=None, chat_id=None, **kwargs):
        self.created = time.time()
        self.user_id = user_id
        self.telegram_chat_id = chat_id
        if kwargs:
            self.oauth_token = kwargs.get('oauth_token')
            self.oauth_token_secret = kwargs.get('oauth_token_secret')
            self.oauth_url = kwargs.get('oauth_url')
            self.callback_key = kwargs.get('callback_key')

    async def save(self):
        data = {}
        for k, v in self.__dict__.items():
            data[k] = getattr(self, k)
        data['_id'] = data['user_id']
        del data['db']
        db = self.db.evernoterobot
        await db.start_sessions.save(data)

    async def find(self, evernote_callback_key: str):
        db = self.db.evernoterobot
        session = await db.start_sessions.find_one(
            {'callback_key': evernote_callback_key})
        if session:
            session['user_id'] = session['_id']
            del session['_id']
            return StartSession(session['user_id'],
                                session['telegram_chat_id'],
                                **session)


class User(Model):

    collection = 'users'

    def __init__(self, user_id=None, access_token=None, notebook_guid=None):
        self.user_id = user_id
        self.evernote_access_token = access_token
        self.notebook_guid = notebook_guid

    async def save(self):
        data = {}
        for k, v in self.__dict__.items():
            data[k] = getattr(self, k)
        data['_id'] = data['user_id']
        del data['db']
        db = self.db.evernoterobot
        await db.users.save(data)
