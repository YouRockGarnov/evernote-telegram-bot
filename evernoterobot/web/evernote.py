from urllib.parse import parse_qs
import traceback

from aiohttp import web

from bot.model import User, StartSession, ModelNotFound


async def oauth_callback(request):
    logger = request.app.logger
    bot = request.app.bot

    try:
        params = parse_qs(request.query_string)
        callback_key = params.get('key', [''])[0]

        session = StartSession.get({'oauth_data.callback_key': callback_key})
        user = User.get({'user_id': session.user_id})

        if params.get('oauth_verifier'):
            oauth_verifier = params['oauth_verifier'][0]
            # TODO: async
            access_token = bot.evernote.get_access_token(
                session.oauth_data['oauth_token'],
                session.oauth_data['oauth_token_secret'],
                oauth_verifier)

            logger.info('evernote access_token = %s' % access_token)

            notebook = bot.evernote.getDefaultNotebook(access_token)

            user.evernote_access_token = access_token
            user.current_notebook = {
                'guid': notebook.guid,
                'name': notebook.name,
            }
            user.save()

            if user.mode == 'one_note' and not hasattr(user, 'places'):
                note_guid = bot.evernote.create_note(
                    access_token, text='', title='Note for Evernoterobot')
                user.places = {
                    user.current_notebook['guid']: note_guid
                }
                user.save()

            text = "Evernote account is connected.\n\
Now you can just send message and note be created.\n\
Current notebook: %s" % notebook.name
            await bot.api.sendMessage(user.telegram_chat_id, text)
        else:
            # User decline access
            logger.info('User declined access. No access token =(')
            text = "We are sorry, but you declined authorization 😢"
            await bot.api.sendMessage(user.telegram_chat_id, text)

    except ModelNotFound as e:
        logger.error(traceback.format_exc())
        logger.error(e)
        return web.HTTPForbidden()
    except Exception:
        logger.fatal(traceback.format_exc())

    return web.HTTPFound(bot.url)
