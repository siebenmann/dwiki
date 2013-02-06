#
# Handle wiki comments.
#
import time, urllib
import hmac

import httputil
import htmlrends, wikirend, template
import views
import rendcache

# The characters that are not legal in comments.
import re
# This is all control characters plus DEL, except \t, \n, and \r.
# See for example http://www.cs.tut.fi/~jkorpela/chars/c0.html
# or http://www.robelle.com/library/smugbook/ascii.html
badcharre = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
def hasbadchars(txt):
	return bool(badcharre.search(txt))		

# This is a bad attempt (that is still better than no attempt) to get
# rid of certain comment spammers. Hopefully it will not get augmented
# much over time; if it does, I really need to find a more general
# mechanism. ('nofollow' on links is not it, for various reasons I
# need to articulate sometime.)
def bannedcontent(txt):
	return ("http://pd2.funnyhost.com" in txt and \
		"http://pd3.funnyhost.com" in txt) or \
	       "http://www.free-naked-girls.info/" in txt or \
	       "http://www.areaseo.com/" in txt or \
	       "http://www.dirare.com" in txt or \
	       "http://poker-hands.50webs.com" in txt or \
	       "http://sitepalace.com/internetpoker" in txt or \
	       "http://home.graffiti.net/poker_room/" in txt or \
	       "http://www.ringtones-dir.com/" in txt or \
	       "http://www.ringtones-rate.com/" in txt or \
	       "http://www.special-ringtones.net/" in txt or \
	       "http://www.la-ringtones.com/" in txt or \
	       "http://www.skincareinfo.us/" in txt or \
	       "http://www.vltinsider.com/" in txt or \
	       "http://www.insurance-top.com/" in txt or \
	       "http://www.wifiplanets.org/" in txt or \
	       "http://www.33game.cn" in txt or \
	       "http://www.computers-guide.co.in/" in txt or \
	       "http://vclosets.com" in txt or \
	       "http://www.progment.com" in txt or \
	       "http://community.babycenter.com/" in txt or \
	       "http://2gbmemory.net" in txt or \
	       "http://gmconference.ca/" in txt or \
	       "http://www.talentclick.com/" in txt or \
	       '<a href="http://' in txt or \
	       '<a href=http://' in txt or \
	       '[url]http://' in txt or \
	       '[url=http://' in txt or \
	       '[url="http://' in txt or \
		txt.startswith("Hello my dear friend! I'm a pure student... ")

# ----
# (Disk) cached retrieval of comments_children(), using rendcache's
# infrastructure. This is a 'flagged heuristic generator', which we
# invalidate any time a comment is posted.
# We also set a context cache for this information in case someone
# is evaluating it repeatedly (as might happen if you use multiple macros
# on a page, for example; in fact the sample front page does this).
def cached_comments_children(context, spage):
	def _ck():
		return ("commentkids", spage.path)
	r = context.getcache(_ck())
	if r is not None:
		return r
	# We avoid a proliferation of small cache entries by skipping
	# the disk cache entirely for single-entry/page requests.
	if not rendcache.cache_on(context.cfg) or spage.type == "file":
		r = list(context.model.comments_children(spage))
		context.setcache(_ck(), r)
		return r

	r = rendcache.get_flagged(context, "comments-kids", spage.path,
				  "comments-updated")
	if r:
		context.setcache(_ck(), r)
		return r
	r = list(context.model.comments_children(spage))
	v = rendcache.Validator()
	# TODO: actual validator? What would it be?
	rendcache.store_gen(context, "comments-kids", spage.path,
			    r, v)
	context.setcache(_ck(), r)
	return r

# Invalidation operation; called from comment posting (whether or not
# the comment store succeeded).
def comment_posted(context):
	rendcache.invalidate_flagged(context, "comments-updated")
# ----

# TODO: this needs revision for IPv6, to say the least. At a minimum
# it should recognize IPv6 IP addresses and turn itself off.

#
# Certain active spammers fetch the write comments page from one IP,
# then submit it a lot from a cluster of other IPs. To make this
# harder, we keep track of the general area of the previous POST
# (currently by sawing off the last IP address and matching on
# that).
def make_ip_field(context):
	ipf = "%s@%d" % (context['remote-ip'], time.time())
	return ipf

# The ':vN' bit on the end is the version of the previp format. Bumping
# it each time insures that a previous generation format cannot verify
# (because it was actually generated by us and thus has a valid signature)
# and go on to explosively confuse the code.
def gen_comment_secret(context):
	if 'global-authseed' in context:
		pref = context['global-authseed']
	else:
		pref = context['wikiname']
	return "%s:%s:comverify:v2" % (pref, context.url(context.page))

# I opt to use hexdigest() instead of base64-encoding the digest because
# it means I do not have to worry about base64 spitting out characters
# that I need to worry about HTML-encoding and de-encoding.
def gen_hash(context, fieldval):
	key = gen_comment_secret(context)
	n = hmac.new(key, fieldval)
	return "%s:%s" % (fieldval, n.hexdigest())

# Validate the previp field. It must be in a valid format (so that we can
# extract the signature and other bits), have a valid signature, be from
# the /24 that this is being submitted from, and not be over 2 hours old.
valid_ipf_re = re.compile("^([0-9.]+)@([0-9]+)\:([a-zA-Z0-9]+)$")
error_var = ":comment:previp:error"
def verify_ip_prefix(context):
	def set_err(msg):
		context.setvar(error_var, msg)
	previp = context.getviewvar('previp')
	if not previp:
		set_err("missing")
		return False

	# Is it in the right format?
	mo = valid_ipf_re.match(previp)
	if not mo:
		set_err("format is bad")
		return False
	# Does the signature match, or has someone been playing monkey
	# games?
	field = "%s@%s" % (mo.group(1), mo.group(2))
	if previp != gen_hash(context, field):
		set_err("signature does not verify")
		return False
	# Since the signature verifies, we know the other fields are good.
	pip, when = mo.group(1), int(mo.group(2))
	# transmogrify the original IP address into a prefix.
	pip = '.'.join(pip.split('.')[:3]) + '.'
	# Is it from the right IP?
	if not context['remote-ip'].startswith(pip):
		set_err("remote IP mismatch")
		return False
	# Is it too old?
	if (when + (2*60*60)) < time.time():
		set_err("is too old")
		return False
	return True

# Generating the prefix is simple.
def gen_ip_prefix(context):
	return gen_hash(context, make_ip_field(context))

def remote_ip_prefix(context):
	return '.'.join(context['remote-ip'].split('.')[:3]) + '.'
def match_ip_prefix(context):
	previp = context.getviewvar('previp')
	if not previp:
		return False
	# Verify that previp is in the proper format.
	n = previp.split('.')
	if len(n) != 4 or n[3] != '':
		return False
	# This works directly, because previp ends with a dot that
	# anchors it.
	return context['remote-ip'].startswith(previp)

import socket
zen_blockon = ('127.0.0.2', '127.0.0.4', '127.0.0.5', '127.0.0.6',
	       '127.0.0.7', '127.0.0.8', )
def check_dnsbl(context):
	n = context['remote-ip'].split('.')
	n.reverse()
	qh = ".".join(n) + '.zen.spamhaus.org.'
	# spamhaus is not talking to us right now
	#qh = ".".join(n) + '.cbl.abuseat.org.'
	try:
		r = socket.gethostbyname_ex(qh)[2]
	except socket.error:
		return False
	for tip in zen_blockon:
		if tip in r:
			return True
	return False

# This renders the data payload as a comment.
def show_comment(data, ctx, flags = 0):
	return wikirend.wikirend(data, ctx, wikirend.NOMACROS | flags)

bad_char_msg = """
<p><b>IMPORTANT: Your comment cannot be displayed because it contains
invalid control characters. Please remove the control characters and
try again.</b></p>
"""

# Render a preview taken from the comment form.
com_bad_var = ":comment:badcomment"
def set_comment_bad(context):
	context.setvar(com_bad_var, True)
def is_comment_bad(context):
	return com_bad_var in context
def commentpreview(context):
	"""In a comment-writing context, show a preview of the comment being
	written."""
	comdata = context.getviewvar("comment")
	#comdata = comtrim(comdata)
	if not comdata:
		return ''
	if context.getviewvar("name"):
		# This is first, so it logs little in what is a common case,
		# and is deliberately obscure.
		context.set_error("name field filled in in comment preview: "+repr(context.getviewvar("name")))
		set_comment_bad(context)
		return bad_char_msg
	if hasbadchars(comdata):
		context.set_error("bad characters in comment preview: " + repr(comdata))
		set_comment_bad(context)
		return bad_char_msg
	if bannedcontent(comdata):
		# The error the user gets is deliberately unclear.
		# We check for bad IP prefix because it results in shorter
		# messages that way.
		if not verify_ip_prefix(context):
			context.set_error("mismatch in comment origin with banned content: previp %s (%s)" % (repr(context.getviewvar('previp')), context[error_var]))
		else:
			context.set_error("banned content in comment preview: " + repr(comdata))
		set_comment_bad(context)
		return bad_char_msg
	# This only triggers if we have comment data to start with, so it
	# will never go off for people just starting to write comments.
	if not verify_ip_prefix(context):
		#context.set_error("mismatch in comment origin: previp %s: %s" % (repr(context.getviewvar('previp')), repr(comdata)))
		context.set_error("info: mismatch in comment origin: previp %s (%s): %s" % (repr(context.getviewvar('previp')), context[error_var], repr(comdata)))
	elif 'comments-report-drafts' in context:
		# We can optionally log all draft comments, even ones that do
		# not fail any of the rules. Note that this is verbose and
		# possibly privacy intrusive.
		context.set_error("info: comment preview contents: %s" % repr(comdata))

	return show_comment(comdata, context)
htmlrends.register("comment::preview", commentpreview)

def commentpre(context):
	"""In a comment-writing context, generate a <pre> block of the
	comment being written."""
	comdata = context.getviewvar("comment")
	#comdata = comtrim(comdata)
	if not comdata:
		return ''
	return "<pre>\n%s</pre>\n" % httputil.quotehtml(comdata)
htmlrends.register("comment::pre", commentpre)


#
# When we generate the form, we have to stamp the current comment
# contents into it so that the user can re-edit them; this happens
# during comment previews.
#
# If the inserted text starts right after the textarea (without a
# newline), we will slowly eat away at blank lines at the start of the
# text (one line per iteration).  If this is desired, we ought to do
# it explicitly, not implicitly.
post_bit = """<input type=submit name=post value="Post Comment">"""
fpreview_bit="""<input type=submit name=dopref value="Visionner votre commentaire">"""
comment_form = """<form method=post action="%s">
<textarea rows='15' cols='75' name='comment'>
%s</textarea> <br>
<span style="display: none;">Please do not enter anything here:
<input name=name size=30> </span>
<input type=hidden name=previp value="%s">
<input type=submit value="Preview Comment">
%s
</form>"""
def commentform(context):
	"""Create the form for writing a new comment in, if the page is
	commentable by the current user."""
	# We are null if we can't comment on the current page.
	if not context.page.comment_ok(context):
		return ''

	comdata = context.getviewvar("comment")
	#comdata = comtrim(comdata)
	if comdata:
		# We have to do the usual quoting of HTML entities.
		# The browser stitches up the result and dequotes
		# them when it gives the whole thing back to us in
		# POST-production.
		comdata = httputil.quotehtml(comdata)
		# We only show the 'post comment' action if there is
		# some comment text already.
		if not is_comment_bad(context):
			post = post_bit
		else:
			post = ''
	else:
		comdata = ''
		#post = fpreview_bit
		post = ''
	curl = context.url(context.page, "writecomment")
	#previp = remote_ip_prefix(context)
	# We only generate a valid previp value if the content is good
	# to start with.
	if not is_comment_bad(context):
		previp = gen_ip_prefix(context)
	else:
		previp = 'omitted'
	data = comment_form % (curl, comdata, previp, post)
	context.unrel_time()
	return data
htmlrends.register("comment::form", commentform)

# Actually, you know, *posting* the comment.
# At this point the user is trying hard to post, so we try to handle
# a number of error conditions internally.
# We return False if the request is mangled.
def post(context, resp):
	# Not even an empty 'comment' data-field supplied means that
	# this is a hand-crafted bogus request.
	comdata = context.getviewvar("comment")
	if comdata is None:
		return False

	# We immediately disallow empty comments.
	#comdata = comtrim(comdata)
	if not comdata:
		context.setvar(":comment:post", "nocomment")
	elif context.getviewvar("name"):
		context.set_error("name field set in comment post: name %s, post %s" % (repr(context.getviewvar("name")), repr(comdata)))
		context.setvar(":comment:post", "bad")
	elif hasbadchars(comdata):
		context.set_error("bad characters in comment post: %s" % repr(comdata))
		context.setvar(":comment:post", "badchars")
	elif bannedcontent(comdata):
		# this is currently deliberately uninformative.
		context.set_error("banned content in comment post: %s" % repr(comdata))
		context.setvar(":comment:post", "bad")
	# disabled, misfired.
	#elif check_dnsbl(context):
	#	# this is also deliberately uninformative.
	#	context.set_error("comment POST from a zen.spamhaus.org-listed IP. Content is: %s" % repr(comdata))
	#	context.setvar(":comment:post", "bad")
	else:
		# post_comment() does permissions checking itself,
		# and the caller has already done it too, so we don't
		# do it a *third* time; we just go straight.
		res = context.model.post_comment(comdata, context)
		if res:
			context.setvar(":comment:post", "good")
		else:
			context.setvar(":comment:post", "bad")
		comment_posted(context)

	# :comment:post now holds what happened, so we let the top
	# level template dispatch off it to figure out what to do.
	to = context.model.get_template("comment/posting.tmpl")
	resp.html(template.Template(to).render(context))
	context.unrel_time()
	return True

# ----
# Comments must be, you know, displayed in order to be really useful.

# Issue: this doesn't set the maximum time. Unfortunately, to do so
# would be somewhat expensive.
def countcomments(context):
	"""Display a count of comments for the current page."""
	if not context.model.comments_on() or \
	   not context.page.access_ok(context):
		return ''

	cl = context.model.get_commentlist(context.page)
	# debateable, but I think that right now no 'no comments'
	# remark looks better.
	if len(cl) == 0:
		return ''
	elif len(cl) == 1:
		return 'One comment'
	else:
		return '%d comments' % len(cl)
htmlrends.register("comment::count", countcomments)

# Generate a link to show the comments.
def _gencountlink(context, abs):
	res = countcomments(context)
	if not res:
		return ''
	f = abs and context.uri or context.url
	url = f(context.page, context.comment_view()) + "#comments"
	return htmlrends.makelink(res, url)
	
def countlink(context):
	"""Display the count of comments as a link to show them for the
	current page."""
	return _gencountlink(context, False)
htmlrends.register("comment::countlink", countlink)

def atomcountlink(context):
	"""Just like _comment::countlink_, except that the URL is absolute
	and the HTML is escaped	so that it can be used in an Atom syndication
	feed."""
	return httputil.quotehtml(_gencountlink(context, True))
htmlrends.register("comment::atomlink", atomcountlink)

# Display comments in a blogdir style thing.
# Unlike blogdir, comments go in oldest-to-newest order.
com_stash_var = ":comment:comment"
def showcomments(context):
	"""Display all of the comments for the current page (if any), using the
	template _comment/comment.tmpl_ for each in succession."""
	if not context.model.comments_on():
		return ''
	# Have I mentioned recently that if you can't see a page you can't
	# see its comments either?
	if not context.page.access_ok(context):
		return ''
	cl = context.model.get_commentlist(context.page)
	if not cl:
		return ''
	# We don't really care too much about the *name* of the comments.
	coms = [context.model.get_comment(context.page, z) for z in cl]
	coms = [z for z in coms if z]
	if not coms:
		return ''
	# Sort into time order.
	coms.sort(lambda x,y: cmp(x.time, y.time))

	# We display using a method similar to blogdir; clone context,
	# set magic variable, render new template.
	to = context.model.get_template("comment/comment.tmpl")
	context.newtime(to.timestamp())
	res = []
	for c in coms:
		nc = context.clone()
		context.newtime(c.time)
		nc.setvar(com_stash_var, c)
		nc.setvar("comment-ip", c.ip)
		nc.setvar("comment-user", c.user)
		res.append(template.Template(to).render(nc))
		context.newtime(nc.modtime)
	return ''.join(res)
htmlrends.register("comment::showall", showcomments)

# Our interior utilities do no permissions checking because the stash
# variable wouldn't exist if they weren't allowed.
def comment(context):
	"""Display a particular comment. Only works inside comment::showall."""
	if com_stash_var not in context:
		return ''
	c = context[com_stash_var]
	context.newtime(c.time)
	return show_comment(c.data, context)
htmlrends.register("comment::comment", comment)

def comdate(context):
	"""Display the date of a comment. Only works inside
	comment::showall."""
	if com_stash_var not in context:
		return ''
	c = context[com_stash_var]
	return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c.time))
htmlrends.register("comment::date", comdate)

# We don't show the user if it's the guest user.
# This is a design decision on my part.
def comuser(context):
	"""Display the user who wrote a comment if it isn't the default
	DWiki user. Only works inside comment::showall."""
	if com_stash_var not in context:
		return ''
	c = context[com_stash_var]
	if c.user == context.default_user():
		return ''
	else:
		return c.user
htmlrends.register("comment::user", comuser)

# In the name of short anchor names, we hope that user + timestamp never
# collides. Using the hash name is ... the ugly.
def anchor_for(c):
	ts = time.strftime("%Y%m%d%H%M%S", time.localtime(c.time))
	atext = "%s-%s" % (c.user, ts)
	return urllib.quote(atext)
def comanchor(context):
	"""Generate an anchor *start* for the current comment.
	You must close the anchor by hand."""
	if com_stash_var not in context:
		return ''
	c = context[com_stash_var]
	return '<a name="%s">' % anchor_for(c)
htmlrends.register("anchor::comment", comanchor)

# Insure newline termination of the comment, because it otherwise
# irritates Chris so. We also replace CRLF with just LF and strip
# off trailing whitespace (leading whitespace can be significant).
# One reason we need to fix CRLF is that wikirend does not expect
# CRLF line terminators, especially when dealing with ' \\'.
#
# Fixing \r\n to \n is necessary in the CGI case, because the cgi
# module does not do this for us (although our generic httputil
# parser *does*, making it hard to see the issue in standalone
# testing; possibly this is a bug).
def comtrim(comdata):
	if not comdata:
		return comdata
	comdata = comdata.rstrip().replace("\r\n", "\n")
	if not comdata or comdata[-1] == '\n':
		return comdata
	else:
		return comdata + "\n"

# View registration.

# A digression on telling what button got pressed:
# When you hit a form submit button that has a name, web
# browsers add a field of the form <button-name>=<button value>
# to the form. The writecomment form's 'Post Comment' button
# has a name of post (and some text value), so we throw it
# into the list of fields we are looking for so we can fish
# it out and declare that this is actually submitting the
# comment. If it is not present, we assume that this is a
# preview or a start-of-comment-writing session.

# Although this has the POST handling, it may be invoked by either
# GET (for the initial startup) or POST (for everything past that).
# It is also attached to the real page, so we can use GenericView's
# respond processing.
class WriteCommentView(views.TemplateView):
	def render(self):
		# Fix up the comment variable globally. This is
		# debatable, but our renderers are already not
		# supposed to be used outside of comment context
		# and this means that they don't all have to call
		# comtrim() themselves. (I think the latter is a
		# code smell.)
		comdata = comtrim(self.context.getviewvar("comment"))
		self.context.setviewvar("comment", comdata)

		# 'post' is the name of the 'Post Comment' button,
		# which is set when we are posting (but not when
		# we are previewing).
		# It is possible to lose comment permissions partway
		# through writing a comment (after you have text and
		# before you hit post). If this happens we fall through
		# to the normal non-post case, which already must handle
		# this; this is somewhat friendlier.
		#
		# If the previous IP address prefix in the POST fails
		# to match, we don't generate any explicit errors; we
		# just fall through to another preview pass.
		#
		#X#   verify_ip_prefix(self.context) and \
		if self.context.getviewvar("post") and \
		   self.context.page.comment_ok(self.context):
			if not post(self.context, self.response):
				self.error("badrequest")
		else:
			# We actually want 100% generic handling here,
			# surprisingly enough.
			super(WriteCommentView, self).render()

views.register('showcomments', views.TemplateView)
views.register('writecomment', WriteCommentView, canPOST = True,
	       postParams = ('comment', 'previp', 'post', 'dopref', 'name'))
