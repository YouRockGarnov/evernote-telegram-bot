import inspect
import importlib
import os
import sys
from os.path import realpath, dirname, join
import traceback
import json

import aiomcache

from telegram.bot import TelegramBot, TelegramBotCommand
from bot.model import StartSession, User
from evernotelib.client import EvernoteClient
import settings


def get_commands(cmd_dir=None):
    commands = []
    if cmd_dir is None:
        cmd_dir = join(realpath(dirname(__file__)), 'commands')
    exclude_modules = ['__init__']
    for dirpath, dirnames, filenames in os.walk(cmd_dir):
        for filename in filenames:
            file_path = join(dirpath, filename)
            ext = file_path.split('.')[-1]
            if ext in ['py']:
                sys_path = list(sys.path)
                sys.path.insert(0, cmd_dir)
                module_name = inspect.getmodulename(file_path)
                if module_name not in exclude_modules:
                    module = importlib.import_module(module_name)
                    sys.path = sys_path
                    for name, klass in inspect.getmembers(module):
                        if inspect.isclass(klass) and\
                           issubclass(klass, TelegramBotCommand) and\
                           klass != TelegramBotCommand:
                            commands.append(klass)
    return commands


class EvernoteBot(TelegramBot):

    def __init__(self, token, name):
        super(EvernoteBot, self).__init__(token, name)
        self.evernote = EvernoteClient(
            settings.EVERNOTE['key'],
            settings.EVERNOTE['secret'],
            settings.EVERNOTE['oauth_callback'],
            sandbox=settings.DEBUG
        )
        self.cache = aiomcache.Client("127.0.0.1", 11211)
        for cmd_class in get_commands():
            self.add_command(cmd_class)

    async def create_start_session(self, user_id, chat_id, oauth_data):
        session = StartSession(
            user_id,
            chat_id,
            oauth_url=oauth_data.oauth_url,
            oauth_token=oauth_data.oauth_token,
            oauth_token_secret=oauth_data.oauth_token_secret,
            callback_key=oauth_data.callback_key)
        await session.save()

    async def get_start_session(self, callback_key):
        return await StartSession().find(callback_key)

    async def register_user(self, start_session, evernote_access_token):
        user_id = start_session.user_id
        await self.cache.set(str(user_id).encode(),
                             evernote_access_token.encode())
        notebook = self.evernote.getNotebook(evernote_access_token)
        await self.cache.set("{0}_nb".format(user_id).encode(),
                             notebook.guid.encode())
        user = User(user_id, evernote_access_token, notebook.guid)
        await user.save()

    async def get_evernote_access_token(self, user_id):
        key = str(user_id).encode()
        token = await self.cache.get(key)
        if token:
            notebook_guid = await self.cache.get(
                "{0}_nb".format(user_id).encode())
            return token.decode(), notebook_guid.decode()
        else:
            entry = await User().find_one({'_id': user_id})
            token = entry['evernote_access_token']
            notebook_guid = entry['notebook_guid']
            await self.cache.set(key, token.encode())
            await self.cache.set("{0}_nb".format(user_id).encode(),
                                 notebook_guid.encode())
            return token, notebook_guid

    async def get_notebook_guid(self, user_id, notebook_name):
        key = "notebook_name_{0}".format(notebook_name).encode()
        guid = await self.cache.get(key)
        if not guid:
            access_token, guid = await self.get_evernote_access_token(user_id)
            for nb in self.evernote.list_notebooks(access_token):
                k = "notebook_name_{0}".format(nb.name).encode()
                await self.cache.set(k, nb.guid.encode())
        guid = await self.cache.get(key)
        return guid.decode()

    async def send_message(self, user_id, text):
        session = await StartSession().find_one({'_id': user_id})
        await self.api.sendMessage(session['telegram_chat_id'], text)

    async def on_text(self, user_id, chat_id, message, text):
        user = await User().get(user_id)
        if user.state == 'select_notebook':
            markup = json.dumps({'hide_keyboard': True})
            await self.api.sendMessage(
                chat_id, 'From now your current notebook is: %s' % text,
                reply_markup=markup)
            user.state = ''
            user.notebook_guid = await self.get_notebook_guid(user_id, text)
            await user.save()
        else:
            reply = await self.api.sendMessage(chat_id, '🔄 Accepted')
            access_token, guid = await self.get_evernote_access_token(user_id)
            self.evernote.create_note(access_token, text, notebook_guid=guid)
            await self.api.editMessageText(chat_id, reply['message_id'],
                                           '✅ Text saved')

    async def on_photo(self, user_id, chat_id, message):
        reply = await self.api.sendMessage(chat_id, '🔄 Accepted')
        caption = message.get('caption', '')
        title = caption or 'Photo'

        files = sorted(message['photo'], key=lambda x: x.get('file_size'),
                       reverse=True)
        file_id = files[0]['file_id']
        filename = await self.api.downloadFile(file_id)
        access_token, guid = await self.get_evernote_access_token(user_id)
        self.evernote.create_note(access_token, caption, title,
                                  files=[(filename, 'image/jpeg')],
                                  notebook_guid=guid)

        await self.api.editMessageText(chat_id, reply['message_id'],
                                       '✅ Image saved')

    async def on_voice(self, user_id, chat_id, message):
        reply = await self.api.sendMessage(chat_id, '🔄 Accepted')
        caption = message.get('caption', '')
        title = caption or 'Voice'

        file_id = message['voice']['file_id']
        ogg_filename = await self.api.downloadFile(file_id)
        wav_filename = ogg_filename + '.wav'
        mime_type = 'audio/wav'
        try:
            # convert to wav
            os.system('opusdec %s %s' % (ogg_filename, wav_filename))
        except Exception:
            self.logger.error("Can't convert ogg to wav, %s" %
                              traceback.format_exc())
            wav_filename = ogg_filename
            mime_type = 'audio/ogg'
        access_token, guid = await self.get_evernote_access_token(user_id)
        self.evernote.create_note(access_token, caption, title,
                                  files=[(wav_filename, mime_type)],
                                  notebook_guid=guid)
        await self.api.editMessageText(chat_id, reply['message_id'],
                                       '✅ Voice saved')

    async def on_location(self, user_id, chat_id, message):
        reply = await self.api.sendMessage(chat_id, '🔄 Accepted')
        # TODO: use google maps API for getting location image
        location = message['location']
        latitude = location['latitude']
        longitude = location['longitude']
        maps_url = "https://maps.google.com/maps?q=%(lat)f,%(lng)f" % {
            'lat': latitude,
            'lng': longitude,
        }
        title = 'Location'
        text = "<a href='%(url)s'>%(url)s</a>" % {'url': maps_url}

        if message.get('venue'):
            address = message['venue'].get('address', '')
            title = message['venue'].get('title', '')
            text = "%(title)s<br />%(address)s<br />\
                <a href='%(url)s'>%(url)s</a>" % {
                'title': title,
                'address': address,
                'url': maps_url
            }
            foursquare_id = message['venue'].get('foursquare_id')
            if foursquare_id:
                url = "https://foursquare.com/v/%s" % foursquare_id
                text += "<br /><a href='%(url)s'>%(url)s</a>" % {'url': url}

        access_token, guid = await self.get_evernote_access_token(user_id)
        self.evernote.create_note(access_token, text, title, notebook_guid=guid)
        await self.api.editMessageText(chat_id, reply['message_id'],
                                       '✅ Location saved')

    async def on_document(self, user_id, chat_id, message):
        reply = await self.api.sendMessage(chat_id, '🔄 Accepted')
        file_id = message['document']['file_id']
        short_file_name = message['document']['file_name']
        file_path = await self.api.downloadFile(file_id)
        mime_type = message['document']['mime_type']

        access_token, guid = await self.get_evernote_access_token(user_id)
        self.evernote.create_note(access_token, '', short_file_name,
                                  files=[(file_path, mime_type)],
                                  notebook_guid=guid)

        await self.api.editMessageText(chat_id, reply['message_id'],
                                       '✅ Document saved')