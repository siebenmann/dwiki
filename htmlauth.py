#
# HTML view authentication services.
#
# The format of the password file is:
#	user	password-sha-hash	[groups ...]
# groups are optional.
#
# We use authcookie to set and recover authenticated login information.
# The secret used for authcookie is the user's password, which arranges
# that a user's cookies go invalid when their password changes.
# The downside of this simple scheme is that it does let anyone who
# just knows the hashed value (perhaps they read the file) masquerade
# as the user.
#
# On the other hand it requires no dynamic storage, which I consider
# a feature. (Ie, it's lots simpler.)

import hashlib

import authcookie, htmlrends, views, template, httputil

#
# A user's encrypted password is the SHA hash of the concatenation of
# their username and their raw password. This insures that different
# people using the same password have password hashes that have
# different values, but it means that you can't copy a password from
# one person to another.
def encryptPassword(user, raw):
	return hashlib.sha1(user + ":" + raw).digest().encode("base64")[:-1]

# This returns the (plaintext) secret for a given user entry in a
# given context.
# Using merely the user's password hash is bad because it opens us
# up to some attacks:
# 1: if I use the same username and password on multiple wikis, my
#    auth cookies will be the same. This is exploitable in various
#    ways; see if passwords are the same if you capture two cookies,
#    capture one cookie and try it on other wikis to see if it works,
#    etc.
# 2: if you can capture a cookie, you can try to brute force my
#    password since you now have a validator for it.
#
# To deal with #1, we introduce per-wiki variability by throwing
# in the wikiname (which we know is always defined).
# To deal with #2, we introduce a per-wiki hidden secret called
# 'global-authseed'. This must be set in the config file, which
# you should insure is not world-readable.
# FIXME: we should be able to get this out of a file. Work for
# later. (It's a model problem; the model loads it from a file
# and adds it to the global variables.)
#
# Changing the global-authseed invalidates all cookies, so we can't
# just make a new one up every time we start or something.
#
def getSecretFor(context, uent):
	if 'global-authseed' in context:
		prefix = context["global-authseed"] + ":"
	else:
		prefix = ""
	return "%s%s:%s" % (prefix, context["wikiname"], uent.pwhash)

# We must try our best to use a different cookie name for each wiki on
# the system, lest logins for one overwrite logins for another and so
# on in to doom.
# (Actually RFC 2109 specifies that they don't. I feel disinclined
# to trust it entirely; besides, this helps with debugging.)
# ... whatdya know, Mozilla does it right. How cute. I'll stick
# with this approach anyways.
def loginCookieName(context):
	return "%s-login" % context.cfg['wikiname']

# We could not set the path and arguably we shouldn't, because we
# may refer to the same wiki through multiple paths (because of,
# eg, CGI-BIN and aliases for same issues).
# ISSUE: the RFC is actually explicit that cookies should only
# be accepted if the path is a prefix of the URI/URL requested.
# This means we lose big (and invisibly) if the user is not
# accessing us through the canonical name that url_root_path()
# will return. authcookie-path is a hack to deal with that.
# ISSUE: with how we're configured, I *think* that if we
# leave everything out the browsers will behave right. I need
# to check the RFC, but it looks like they decide that the
# directory that the URI of the cookie-setting request is
# the proper 'path' value, which is exactly what we want since
# we use synthetic URLs in the root.
secondsYear = 60*60*24*365
def setupCookie(context, resp, user, secret):
	lcn = loginCookieName(context)
	authcookie.setCookie(resp.cookie, lcn, user, secret)

	# I think omitting 'path' does the right thing by
	# default.
	if "authcookie-path" in context:
		acp = context["authcookie-path"]
		if acp is True:
			acp = context.model.get_page("")
			acp = context.url(acp)
		resp.cookie[lcn]['path'] = acp

	# The Cookie module sets expires as a delta time if
	# you give it an integer. We opt to expire in a year.
	# We could expire faster if we renewed cookies on
	# each request.
	# 'expires' is the original Netscape spec, 'max-age' is
	# the modern one. We set both to be thorough.
	resp.cookie[lcn]['expires'] = secondsYear
	resp.cookie[lcn]['max-age'] = secondsYear
	resp.cookie[lcn]['httponly'] = True
	if context['server-url'].startswith("https:"):
		resp.cookie[lcn]['secure'] = True
	
# Destroy the user's login cookie by replacing it with an invalid one.
# Because of how we format the password file we are assured that a
# space can never appear as part of the hashed password, so this is
# 100% guaranteed to not verify even if there *is* a user NOLOGIN.
def destroyUserCookie(context, resp):
	setupCookie(context, resp, "NOLOGIN", "BOGUS SECRET")
	# 'expiry' in the past, or a max-age of 0, is the way to tell
	# browsers to delete the cookie. (Overwriting the login info
	# with NOLOGIN just makes it sure.)
	lcn = loginCookieName(context)
	resp.cookie[lcn]['expires'] = -secondsYear
	resp.cookie[lcn]['max-age'] = 0

# If the context is logged in as a particular user, save a cookie
# authenticating this to the response.
def setUserCookie(context, resp):
	if not context.login:
		return
	user = context.login
	uent = context.current_user()

	# If the user is not in the password file, we decide not to make
	# this a fatal error but instead log the user out. We do this
	# (until I can figure out a better way) by setting a bogus cookie.
	if not uent:
		destroyUserCookie(context, resp)
	else:
		setupCookie(context, resp, user, getSecretFor(context, uent))

# Attempt to recover the active login from the cookie passed in to us.
def setLoginFromCookie(context, cookie):
	lcn = loginCookieName(context)
	if lcn not in cookie:
		return
	(user, auth) = authcookie.splitCookie(cookie, lcn)
	if not user:
		return
	uent = context.model.get_user(user)
	if not uent:
		return
	# Okay, we have a *plausible* case, and better yet we know
	# enough to recover the secret and try to validate.
	res = authcookie.valFromCookie(cookie, lcn,
				       getSecretFor(context, uent))
	if res != user:
		return
	# Validated. Set.
	context.do_login(user)

# Try to authenticate against a user/password combination.
# Unlike previous things we return true/false to determine if we
# worked or failed.
def setLoginFromPassword(context, user, password):
	if not (user and password):
		return False
	uent = context.model.get_user(user)
	if not uent:
		# As a hack, we can report bad logins for nonexistent
		# usernames, because they are often people trying to
		# file comment spam. (!!)
		if context.get('logins-report-bad', False):
			if len(user) > 50:
				user = user[:50] + " <truncated>"
			context.set_error("warning: bad login. login name: " + repr(user))
		return False
	if uent.pwhash == encryptPassword(uent.user, password):
		context.do_login(user)
		return True
	else:
		return False

# Authentication-related renderers.

# The login box gives you either a 'login' or a 'logout' form, depending
# on whether you're logged in as a non-default user or not. If the wiki
# is not configured for authentication, you get nothing.
# The truly annoying depths of HTTP make me choke in agony and
# irritation.
loginBoxForm = """<form method=post action="%s">
Login: <input name=login size=10>
Password: <input type=password name=password size=10>
<input type=hidden name=view value=login>
<input type=hidden name=page value="%s">
<input type=submit value="Login"></form>"""
logoutBoxForm = """<form method=post action="%s">
<input type=hidden name=view value=logout>
<input type=hidden name=page value="%s">
<input type=submit value="Logout"></form>"""
def loginbox(context):
	"""Generate the form for a login or logout box. Generates nothing
	if DWiki authentication is disabled. As a side effect, kills page
	modification time if it generates anything."""
	# If the wiki is not authentication-enabled, you get nothing.
	if not context.model.has_authentication():
		return ''

	# Login versus non-login makes last-modified unreliable, at
	# least for pages where we render the login box stuff.
	context.unrel_time()

	ppath = context.page.path
	# Overwriting ppath with :post:page insures that when we are
	# on virtual pages (perhaps because a login failed) and
	# we resubmit the form, we go to the right place instead
	# of a nonexistent virtual page in the normal view, which
	# would *really* confuse the users.
	if ":post:page" in context:
		ppath = context[":post:page"]
	# NOTE: we do NOT supply the *url* to the page; we supply
	# the *page*. The difference is crucial if the two are not
	# the same, because we need the latter.

	# Are we logged in?
	# (We have to use url_from_path instead of getting pages, because
	#  we can't *get* these as valid pages. That's the point of the
	#  synthetic names.)
	qppath = httputil.quotehtml(ppath)
	if context.current_user() and not context.is_login_default():
		turl = context.web.url_from_path(".logout")
		return logoutBoxForm % (turl, qppath)
	else:
		turl = context.web.url_from_path(".login")
		return loginBoxForm % (turl, qppath)
	# Fortunately I don't have to figure out how to add values
	# in the URL right now, because this is (ta-dah) a POST form.
htmlrends.register("auth::loginbox", loginbox)

# Render in a link to the :post:page variable.
def postlink(context):
	"""Generate a link to the origin page for a POST request
	in a POST form context."""
	if not ":post:page" in context:
		return ''
	pp = context.model.get_page(context[":post:page"])
	return htmlrends.makelink(pp.path, context.nurl(pp))
htmlrends.register("post::oldpage", postlink)

# -----
# View registration and other view things.

login_msg = "Your login attempt was unsuccessful. Please try again. (We apologize for the terseness here, but our normal friendly login failure page is broken.)"
# This is not an actual error; we generate a real, non 404 page.
# Because it is annoying to generate, we put it out of line here.
def loginerror(ctx, resp):
	ctx.unrel_time()
	to = ctx.model.get_template("login-error.tmpl", False)
	if not to:
		resp.error("Your login attempt was unsuccessful. Please try again. (We apologize for the terseness here, but our normal friendly login failure page is broken.)")
	else:
		resp.html(template.Template(to).render(ctx))

# There are two requirements for getting a nice redirection out of POST
# form submissions:
# 1: you must be redirecting to a different URL (or at least lynx explodes)
# 2: you must use a code 302, not a 301.
# In theory a fully HTTP/1.1 compliant environment should use a 303.
def send_location(ctx, resp):
	# Note that pre HTTP/1.0 people will just lose madly here
	# anyways, because we don't write text versions of redirects
	# (although we perhaps should; we could automate it).
	if ctx["http-version"] == "HTTP/1.0":
		resp.code = 302
	else:
		resp.code = 303
	page = ctx.model.get_page(ctx[":post:page"])
	resp.headers['Location'] = ctx.nuri(page)
	ctx.unrel_time()

class LoginView(views.PostView):
	post_vars = ("login", "password", "page")
	def post(self):
		login = self.context.getviewvar("login")
		pw = self.context.getviewvar("password")
		res = setLoginFromPassword(self.context, login, pw)
		# If we succeed, we set the cookie and immediately return
		# a redirect to the normal page. Otherwise we try to show
		# a nice error (and fall back to a much less nice one if
		# we really have to).
		if res:
			setUserCookie(self.context, self.response)
			send_location(self.context, self.response)
		else:
			loginerror(self.context, self.response)

# We inherit the default post vars of 'page'.
class LogoutView(views.PostView):
	def post(self):
		destroyUserCookie(self.context, self.response)
		self.context.logout()
		send_location(self.context, self.response)

views.register('login', LoginView, canPOST = True, canGET = False,
	       postParams = ('login', 'password', 'page',))
views.register('logout', LogoutView, canPOST = True, canGET = False,
	       postParams = ('page',))

	       
# As a convenience, spit out the encoded version of the passwords on the
# command line.
# If we are given no arguments, read 20 bytes of /dev/urandom and barf
# them out in base64 to serve as a decent global-authseed value.
if __name__ == "__main__":
	import sys
	if len(sys.argv) < 2:
		fp = open("/dev/urandom", "rb")
		buf = fp.read(20)
		print buf.encode("base64")[:-1]
	else:
		args = sys.argv[1:]
		if len(args) % 2 != 0:
			print "usage: %s user password [user password ...]"
			sys.exit(1)
		while args:
			user = args.pop(0)
			pw = args.pop(0)
			print "%s: %s" % (user, encryptPassword(user, pw))
