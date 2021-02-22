# -*- coding: utf-8 -*-

import os
import re
import time
import bcrypt
import asyncio
import hashlib
import markdown2
from cmyui import log, Ansi
from quart import Blueprint, render_template, redirect, request, session

from objects import glob
from objects.privileges import Privileges
from objects.utils import flash, get_safe_name, fetch_geoloc

__all__ = ()

frontend = Blueprint('frontend', __name__)

""" valid modes, mods, sorts """
valid_modes = frozenset({'std', 'taiko', 'catch', 'mania'})
valid_mods = frozenset({'vn', 'rx', 'ap'})
valid_sorts = frozenset({'tscore', 'rscore', 'pp', 'plays',
                        'playtime', 'acc', 'maxcombo'})

""" regex """
_username_rgx = re.compile(r'^[\w \[\]-]{2,15}$')
_email_rgx = re.compile(r'^[^@\s]{1,200}@[^@\s\.]{1,30}\.[^@\.\s]{1,24}$')

""" home """
@frontend.route('/home') # GET
@frontend.route('/')
async def home():
    return await render_template('home.html')

""" settings """
@frontend.route('/settings') # GET
@frontend.route('/settings/profile') # GET
async def settings_profile():
    # if not authenticated; render login
    if not 'authenticated' in session:
        return await flash('error', 'You must be logged in to access profile settings!', 'login')

    return await render_template('settings/profile.html')
@frontend.route('/settings/profile', methods=['POST']) # POST
async def settings_profile_post():
    # if not authenticated; render login
    if not 'authenticated' in session:
        return await flash('error', 'You must be logged in to access profile settings!', 'login')
    
    form = await request.form

    username = form.get('username')
    email = form.get('email')

    # deny post if no data has changed
    if username == session['user_data']['name'] and email == session['user_data']['email']:
        return await flash('error', 'No changes have been made.', 'settings/profile')

    # Usernames must:
    # - be within 2-15 characters in length
    # - not contain both ' ' and '_', one is fine
    # - not be in the config's `disallowed_names` list
    # - not already be taken by another player
    if not _username_rgx.match(username):
        return await flash('error', 'Your new username syntax is invalid.', 'settings/profile')

    if '_' in username and ' ' in username:
        return await flash('error', 'Your new username may contain "_" or " ", but not both.', 'settings/profile')

    if username in glob.config.disallowed_names:
        return await flash('error', 'Your new username isn\'t allowed; pick another.', 'settings/profile')

    if await glob.db.fetch('SELECT 1 FROM users WHERE name = %s AND NOT name = %s', [username, session['user_data']['name']]):
        return await flash('error', 'Your new username already taken by another user.', 'settings/profile')

    # Emails must:
    # - match the regex `^[^@\s]{1,200}@[^@\s\.]{1,30}\.[^@\.\s]{1,24}$`
    # - not already be taken by another player
    if not _email_rgx.match(email):
        return await flash('error', 'Your new email syntax is invalid.', 'settings/profile')

    if await glob.db.fetch('SELECT 1 FROM users WHERE email = %s AND NOT email = %s', [email, session['user_data']['email']]):
        return await flash('error', 'Your new email already taken by another user.', 'settings/profile')

    # username change successful
    if username != session['user_data']['name']:
        await glob.db.execute('UPDATE users SET name = %s, safe_name = %s WHERE safe_name = %s', [username, get_safe_name(username), get_safe_name(session['user_data']['name'])])
    
    # email change successful
    if email != session['user_data']['email']:
        safe_name = get_safe_name(username) if username != session['user_data']['name'] else get_safe_name(session['user_data']['name'])
        await glob.db.execute('UPDATE users SET email = %s WHERE safe_name = %s', [email, safe_name])

    # logout
    session.pop('authenticated', None)
    session.pop('user_data', None)
    return await flash('success', 'Your username/email have been changed! Please login again.', 'login')
@frontend.route('/settings/avatar') # GET
async def settings_avatar():
    # if not authenticated; render login
    if not 'authenticated' in session:
        return await flash('error', 'You must be logged in to access avatar settings!', 'login')

    return await render_template('settings/avatar.html')
@frontend.route('/settings/password') # GET
async def settings_password():
    # if not authenticated; render login
    if not 'authenticated' in session:
        return await flash('error', 'You must be logged in to access password settings!', 'login')    

    return await render_template('settings/password.html')
@frontend.route('/settings/password', methods=["POST"]) # POST
async def settings_password_post():
    # if not authenticated; render login
    if not 'authenticated' in session:
        return await flash('error', 'You must be logged in to access password settings!', 'login')   
    
    form = await request.form

    old_password = form.get('old_password')
    new_password = form.get('new_password')
    repeat_password = form.get('repeat_password')

    bcrypt_cache = glob.cache['bcrypt']
    pw_bcrypt = (await glob.db.fetch('SELECT pw_bcrypt FROM users WHERE safe_name = %s', [get_safe_name(session['user_data']['name'])]))['pw_bcrypt'].encode()
    pw_md5 = hashlib.md5(old_password.encode()).hexdigest().encode()

    # check old password against db
    # intentionally slow, will cache to speed up
    if pw_bcrypt in bcrypt_cache:
        if pw_md5 != bcrypt_cache[pw_bcrypt]: # ~0.1ms
            if glob.config.debug:
                log(f'{session["user_data"]["name"]}\'s change pw failed - pw incorrect.', Ansi.LYELLOW)
            return await flash('error', 'Your old password is incorrect.', 'settings/password')
    else: # ~200ms
        if not bcrypt.checkpw(pw_md5, pw_bcrypt):
            if glob.config.debug:
                log(f'{session["user_data"]["name"]}\'s change pw failed - pw incorrect.', Ansi.LYELLOW)
            return await flash('error', 'Your old password is incorrect.', 'settings/password')

    # new password and old password match; deny password change
    if old_password.lower() == new_password.lower():
        return await flash('error', 'Your new password cannot be the same as your old password!', 'settings/password')

    # new password and repeat password don't match; deny password change
    if new_password != repeat_password:
        return await flash('error', "Your new password doesn't match your repeated password!", 'settings/password')

    # Passwords must:
    # - be within 8-32 characters in length
    # - have more than 3 unique characters
    # - not be in the config's `disallowed_passwords` list
    if not 8 < len(new_password) <= 32:
        return await flash('error', 'Your new password must be 8-32 characters in length.', 'settings/password')

    if len(set(new_password)) <= 3:
        return await flash('error', 'Your new password must have more than 3 unique characters.', 'settings/password')

    if new_password.lower() in glob.config.disallowed_passwords:
        return await flash('error', 'Your new password was deemed too simple.', 'settings/password')

    # remove old password from cache
    if pw_bcrypt in bcrypt_cache:
        del bcrypt_cache[pw_bcrypt] 

    # update password in cache and db    
    pw_md5 = hashlib.md5(new_password.encode()).hexdigest().encode()
    pw_bcrypt = bcrypt.hashpw(pw_md5, bcrypt.gensalt())
    await glob.db.execute('UPDATE users SET pw_bcrypt = %s WHERE safe_name = %s', [pw_bcrypt, get_safe_name(session['user_data']['name'])])
    bcrypt_cache[pw_bcrypt] = pw_md5

    # logout
    session.pop('authenticated', None)
    session.pop('user_data', None)
    return await flash('success', 'Your password has been changed! Please login again.', 'login')

""" profile """
@frontend.route('/u/<id>') # GET
async def profile(id):
    mode = request.args.get('mode', type=str)
    mods = request.args.get('mods', type=str)
    
    if mods:
        if mods not in valid_mods:
            return b'invalid mods! (vn, rx, ap)'
    else:
        mods = 'vn'
    if mode:
        if mode not in valid_modes:
            return b'invalid mode! (std, taiko, catch, mania)'
    else:
        mode = 'std'

    userdata = await glob.db.fetch(f'SELECT name, id, priv, country FROM users WHERE id = {id}')

    # don't display profile if user is banned
    is_staff = 'authenticated' in session and session['user_data']['is_staff']
    if not userdata or not (userdata['priv'] & Privileges.Normal or is_staff):
        return await render_template('404.html')

    return await render_template('profile.html', user=userdata, mode=mode, mods=mods)

""" leaderboard """
@frontend.route('/leaderboard') # GET
async def leaderboard_nodata():
    return await render_template('leaderboard.html', mode='std', sort='pp', mods='vn')
@frontend.route('/leaderboard/<mode>/<sort>/<mods>') # GET
async def leaderboard(mode, sort, mods):
    return await render_template('leaderboard.html', mode=mode, sort=sort, mods=mods)

""" login """
@frontend.route('/login') # GET
async def login():
    # if authenticated; render home
    if 'authenticated' in session:
        return await flash('error', f'Hey! You\'re already logged in {session["user_data"]["name"]}!', 'home')

    return await render_template('login.html')
@frontend.route('/login', methods=['POST']) # POST
async def login_post():
    # if authenticated; deny post; return
    if 'authenticated' in session:
        return await flash('error', f'Hey! You\'re already logged in {session["user_data"]["name"]}!', 'home')

    login_time = time.time_ns() if glob.config.debug else 0

    form = await request.form
    username = form.get('username')

    # check if account exists
    user_info = await glob.db.fetch(
        'SELECT id, name, email, priv, pw_bcrypt, silence_end '
        'FROM users WHERE safe_name = %s',
        [get_safe_name(username)]
    )

    # the second part of this if statement exists because if we try to login with Aika
    # and compare our password input against the database it will fail because the
    # hash saved in the database is invalid.
    if not user_info or user_info['id'] == 1:
        if glob.config.debug:
            log(f'{username}\'s login failed - account doesn\'t exist.', Ansi.LYELLOW)
        return await flash('error', 'Account does not exist.', 'login')

    bcrypt_cache = glob.cache['bcrypt']

    pw_bcrypt = user_info['pw_bcrypt'].encode()
    pw_md5 = hashlib.md5(form.get('password').encode()).hexdigest().encode()

    # check credentials (password) against db
    # intentionally slow, will cache to speed up
    if pw_bcrypt in bcrypt_cache:
        if pw_md5 != bcrypt_cache[pw_bcrypt]: # ~0.1ms
            if glob.config.debug:
                log(f'{username}\'s login failed - pw incorrect.', Ansi.LYELLOW)
            return await flash('error', 'Password is incorrect.', 'login')
    else: # ~200ms
        if not bcrypt.checkpw(pw_md5, pw_bcrypt):
            if glob.config.debug:
                log(f'{username}\'s login failed - pw incorrect.', Ansi.LYELLOW)
            return await flash('error', 'Password is incorrect.', 'login')

        # login successful; cache password for next login
        bcrypt_cache[pw_bcrypt] = pw_md5

    # user not verified
    if not user_info['priv'] & Privileges.Verified:
        if glob.config.debug:
            log(f'{username}\'s login failed - not verified.', Ansi.LYELLOW)
        return await render_template('verify.html')

    # user banned
    if not user_info['priv'] & Privileges.Normal:
        if glob.config.debug:
            log(f'{username}\'s login failed - banned.', Ansi.RED)
        return await flash('error', 'You are banned!', 'login')

    # login successful; store session data
    if glob.config.debug:
        log(f'{username}\'s login succeeded.', Ansi.LGREEN)

    session['authenticated'] = True
    session['user_data'] = {
        'id': user_info['id'],
        'name': user_info['name'],
        'email': user_info['email'],
        'priv': user_info['priv'],
        'silence_end': user_info['silence_end'],
        'is_staff': user_info['priv'] & Privileges.Staff,
        'is_donator': user_info['priv'] & Privileges.Donator
    }

    if glob.config.debug:
        login_time = (time.time_ns() - login_time) / 1e6
        log(f'Login took {login_time:.2f}ms!', Ansi.LYELLOW)

    # authentication successful; redirect home
    return await flash('success', f'Hey! Welcome back {username}!', 'home')

""" registration """
@frontend.route('/register') # GET
async def register():
    # if authenticated; redirect home
    if 'authenticated' in session:
        return await flash('error', f'Hey! You\'re already registered and logged in {session["user_data"]["name"]}!', 'home')

    # if registration is disabled; redirect home
    if not glob.config.registration:
        return await flash('error', 'Hey! You can\'t register at this time! Sorry for the inconvenience!', 'home')
    
    return await render_template('register.html')
@frontend.route('/register', methods=['POST']) # POST
async def register_post():
    # if authenticated; deny post; return
    if 'authenticated' in session:
        return await flash('error', f'Hey! You\'re already registered and logged in {session["user_data"]["name"]}!', 'home')

    # if registration is disabled; deny post; return
    if not glob.config.registration:
        return await flash('error', 'Hey! You can\'t register at this time! Sorry for the inconvenience!', 'home')

    # get form data (username, email, password)
    form = await request.form
    username = form.get('username')
    email = form.get('email')
    pw_txt = form.get('password')

    # Usernames must:
    # - be within 2-15 characters in length
    # - not contain both ' ' and '_', one is fine
    # - not be in the config's `disallowed_names` list
    # - not already be taken by another player
    # check if username exists
    if not _username_rgx.match(username):
        return await flash('error', 'Invalid username syntax.', 'register')

    if '_' in username and ' ' in username:
        return await flash('error', 'Username may contain "_" or " ", but not both.', 'register')

    if username in glob.config.disallowed_names:
        return await flash('error', 'Disallowed username; pick another.', 'register')

    if await glob.db.fetch('SELECT 1 FROM users WHERE name = %s', username):
        return await flash('error', 'Username already taken by another user.', 'register')

    # Emails must:
    # - match the regex `^[^@\s]{1,200}@[^@\s\.]{1,30}\.[^@\.\s]{1,24}$`
    # - not already be taken by another player
    if not _email_rgx.match(email):
        return await flash('error', 'Invalid email syntax.', 'register')

    if await glob.db.fetch('SELECT 1 FROM users WHERE email = %s', email):
        return await flash('error', 'Email already taken by another user.', 'register')

    # Passwords must:
    # - be within 8-32 characters in length
    # - have more than 3 unique characters
    # - not be in the config's `disallowed_passwords` list
    if not 8 < len(pw_txt) <= 32:
        return await flash('error', 'Password must be 8-32 characters in length', 'register')

    if len(set(pw_txt)) <= 3:
        return await flash('error', 'Password must have more than 3 unique characters.', 'register')

    if pw_txt.lower() in glob.config.disallowed_passwords:
        return await flash('error', 'That password was deemed too simple.', 'register')

    async with asyncio.Lock():
        pw_md5 = hashlib.md5(pw_txt.encode()).hexdigest().encode()
        pw_bcrypt = bcrypt.hashpw(pw_md5, bcrypt.gensalt())
        glob.cache['bcrypt'][pw_bcrypt] = pw_md5 # cache result for login

        safe_name = get_safe_name(username)
        
        # fetch the users' country
        if request.headers and (ip := request.headers.get('X-Real-IP')):
            country = await fetch_geoloc(ip)
        else:
            country = 'xx'

        # add to `users` table.
        user_id = await glob.db.execute(
            'INSERT INTO users '
            '(name, safe_name, email, pw_bcrypt, country, creation_time, latest_activity) '
            'VALUES (%s, %s, %s, %s, %s, UNIX_TIMESTAMP(), UNIX_TIMESTAMP())',
            [username, safe_name, email, pw_bcrypt, country]
        )

        # add to `stats` table.
        await glob.db.execute(
            'INSERT INTO stats '
            '(id) VALUES (%s)',
            [user_id]
        )

    if glob.config.debug:
        log(f'{username} has registered - awaiting verification.', Ansi.LGREEN)

    # user has successfully registered
    return await render_template('verify.html')

""" logout """
@frontend.route('/logout') # GET
async def logout():
    if not 'authenticated' in session:
        return await flash('error', 'You can\'t logout if you aren\'t logged in!', 'login')

    if glob.config.debug:
        log(f'{session["user_data"]["name"]} logged out.', Ansi.LGREEN)

    # clear session data
    session.pop('authenticated', None)
    session.pop('user_data', None)

    # render login
    return await flash('success', 'Successfully logged out!', 'login')

""" docs """
@frontend.route('/docs') # GET
async def docs_nodata():
    docs = []
    async with asyncio.Lock():
        for f in os.listdir('docs/'):
            docs.append(os.path.splitext(f)[0])

    return await render_template('docs.html', docs=docs)
@frontend.route('/doc/<doc>') # GET
async def docs(doc):
    async with asyncio.Lock():
        markdown = markdown2.markdown_path(f'docs/{doc.lower()}.md')
    return await render_template('doc.html', doc=markdown, doc_title=doc.lower().capitalize())

""" social media redirects """
@frontend.route('/github') # GET
@frontend.route('/gh')
async def github_redirect():
    return redirect(glob.config.github)
@frontend.route('/discord') # GET
async def discord_redirect():
    return redirect(glob.config.discord_server)
@frontend.route('/youtube') # GET
@frontend.route('/yt') # GET
async def youtube_redirect():
    return redirect(glob.config.youtube)
@frontend.route('/twitter') # GET
async def twitter_redirect():
    return redirect(glob.config.twitter)
@frontend.route('/instagram') # GET
@frontend.route('/ig') # GET
async def instagram_redirect():
    return redirect(glob.config.instagram)
