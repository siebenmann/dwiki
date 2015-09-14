#
# HTML renderers register themselves here.
# (This is not in the HTML view stuff because of import recursion issues)
# Who is registered is a global setting.
# All renderers are called the same way: renderer(context)
# They return the result or raise derrors.RendErr if they fail
# (sufficiently badly).
# Note that wiki rendering has to do additional work to get the data
# it needs (off the page).
# Note that template handling is not considered a renderer, because
# it requires an additional argument (the template name) so it is handled
# out of line.
import time, urllib
import derrors, httputil

reg_renderers = {}

def register(name, callable):
	reg_renderers[name] = callable
def get_renderer(name):
	if name in reg_renderers:
		return reg_renderers[name]
	else:
		raise derrors.RendErr("renderer '%s' not available" % name)
def all_renderers():
	return reg_renderers.keys()

#
# ---
# Some HTML renderers too small to call for their own file(s).
#

# So many people use this that I should make it a utility routine.
def makelink(text, dest, nofollow = False, ctype = None):
	if not text:
		text = '/'
	nfstr = ''; ctstr = ''
	if nofollow:
		nfstr = ' rel="nofollow"'
	if ctype:
		ctstr = ' type="%s"' % ctype
	return '<a%s href="%s"%s>%s</a>' % (ctstr, dest, nfstr,
					    httputil.quotehtml(text))

# Create a little set of 'breadcrumbs' links, presumably at the top.
# The wiki's root is always a link, even if we are at the Wiki's root
# page (whatever that is), because I think that's more consistent.
def breadcrumbs(context):
	"""Display a 'breadcrumbs' hierarchy of links from the DWiki root
	to the current page."""
	result = []
	result.append('<span class="breadcrumbs">')
	result.append(makelink(context.cfg['wikiname'],
			       context.web.url_from_path("")))

	# We don't generate breadcrumbs at the root.
	# We need to check context.page.path too because we may be
	# rendering for the root directory in some contexts.
	if context.page.path != context.wiki_root() and context.page.path:
		tl = []
		curpage = context.page
		while curpage.path != '':
			tl.append(curpage)
			curpage = curpage.parent()
		tl.reverse()
		last = tl.pop()
		skippingPages = False
		for page in tl:
			if not page.exists() and skippingPages:
				continue
			result.append(" &raquo;\n       ")
			if not page.exists():
				result.append("....")
				skippingPages = True
				continue
			# Virtual pages breadcrumb in the current view,
			# because this is what you really want.
			if page.virtual():
				result.append(makelink(page.name,
						       context.url(page)))
			else:
				result.append(makelink(page.name,
						       context.nurl(page)))
		# Last entry is not a link; it's where we *are*.
		# Making it a link is a) redundant and b) slightly confusing
		# and c) not how I've done breadcrumbs by hand in the past.
		# I like my old way, so we do it this way automatically.
		result.append(" &raquo;\n       ")
		result.append(httputil.quotehtml(last.name))

	result.append("</span>")
	return ''.join(result)
register('breadcrumbs', breadcrumbs)

# This is sort of the inverse of breadcrumbs: everywhere we can go down.
# This is more or less like wikirend's macro_ListDir.
def listofdirs(context):
	"""Display a list of the subdirectories in the current directory."""
	curdir = context.page.curdir()
	# Fancy how this ... just works.
	fc = curdir.children("dir")
	if not fc:
		return ''
	fc = [makelink(z.name, context.url(z)) for z in fc]
	return ', '.join(fc)
register('listofdirs', listofdirs)

# Render a link to this page, using either the full path or the
# page name as the link text.
# ISSUE: should we use .url or .nurl? Not sure.
def linktoself(context):
	"""A link to this page, titled with the full page path."""
	return makelink(context.page.me().path, context.url(context.page.me()))
register("linktoself", linktoself)
def shortlink(context):
	"""A link to this page, titled with the page's name."""
	return makelink(context.page.me().name, context.url(context.page.me()))
register("linkshort", shortlink)
# Shortlink, but forced to the normal view.
def nshortlink(context):
	"""A link to this page in the normal view, titled with the page's
	name."""
	return makelink(context.page.me().name,
			context.nurl(context.page.me()))
register("linkshortnormal", nshortlink)
def linktonormalself(context):
	"""A link to this page in the normal view, titled with the full
	page path."""
	return makelink(context.page.me().path,
			context.nurl(context.page.me()))
register("linktonormal", linktonormalself)			

def commentlink(context):
	"""Create a link to this page that will show comments (if any).
	Otherwise the same as _linktonormal_."""
	url = context.url(context.page, context.comment_view())
	return makelink(context.page.me().path, url)
register("linktocomments", commentlink)

# Uses the 'relname' context variable, if defined, and otherwise
# punts to linktoself.
def relnamelink(context):
	"""Inside blog::blog, generate a link to this page titled with
	the page's path relative to the blog::blog page. Outside that
	context, the same as linktoself."""
	if 'relname' not in context:
		return linktoself(context)
	return makelink(context['relname'], context.url(context.page.me()))
register("linkrelname", relnamelink)

def rooturl(context):
	"""Generate the URL to the root of this DWiki."""
	return context.url(context.model.get_page(""))
register("rooturl", rooturl)

# Creates an anchor for the current page's name.
def anchorself(context):
	"""Generates an anchor *start* where the name is the full path
	to the current page. You must close the anchor by hand."""
	return '<a name="%s">' % urllib.quote(context.page.path)
register("anchor::self", anchorself)
def anchorshort(context):
	"""Generates an anchor *start* where the name is the name of
	the current page. You must close the anchor by hand."""
	return '<a name="%s">' % urllib.quote(context.page.name)
register("anchor::short", anchorshort)

# ---
# Link tools: links to various other formats (source, history, etc).
def linktosource(context):
	"""Generate a link to this page's source called 'View Source', if it
	has any and you can see it."""
	if not context.page.has_source() or \
	   not context.page.displayable() or \
	   not context.page.access_ok(context):
		return ''
	return makelink("View Source", context.url(context.page, "source"),
			True)
register("linksource", linktosource)

def linktohistory(context):
	"""Generate a link to this page's history called 'View History', if
	it has any."""
	if not context.page.hashistory():
		return ''
	if context['view-format'] == "history":
		return ''
	return makelink("View History", context.url(context.page, "history"))
register("linkhistory", linktohistory)

def linktonormal(context):
	"""Generate a link to this page's normal view called 'View Normal'
	if it is a file and we are not displaying it in normal view."""
	if context.page.type != "file" or \
	   context['view-format'] == "normal":
		return ''
	return makelink("View Normal", context.nurl(context.page))
register("linknormal", linktonormal)

# Link to all the alternative views of directories than the current one.
def linktoaltdirviews(context):
	"""Generate a list of links to acceptable alternate ways to view
	the page if it is a directory."""
	if context.page.type != "dir":
		return ''
	curview = context.view
	pv = context.pref_view(context.page)
	altlist = context.web.all_dir_views()
	# If the current view is not in the directory views, we are
	# up to something funny in the land of GET and POST.
	# (The specific case that triggered this is search, which uses
	# a synthetic view hooked on the root.)
	if curview not in altlist:
		return ''
	altlist.remove(curview)
	altlist.sort()
	res = []
	for view in altlist:
		if not context.web.page_view_ok(context, context.page, view):
			continue
		# We mark directory links to non-default formats as
		# nofollow, to keep Google and friends from indexing
		# redundant data repeatedly (especially since it is
		# the non-preferred format).
		if view == pv or \
		   view == "normal" and not pv:
			nurl = context.nurl(context.page)
			nf = False
		else:
			nurl = context.url(context.page, view)
			nf = True
		view = view.capitalize()
		res.append(makelink("See As %s" % view, nurl, nf))
	return ', '.join(res)
register("dir::altviews", linktoaltdirviews)
		
# Write Comment link.
def writecomment(context):
	"""Generate a link to start writing comments on the current page,
	if the current user can comment on the page."""
	if not context.page.comment_ok(context):
		return ''
	curl = context.url(context.page, "writecomment")
	return makelink("Add Comment", curl, True)
register("comment::write", writecomment)

ptools = (linktosource, linktohistory, linktonormal,
	  linktoaltdirviews, writecomment, )
def pagetools(context):
	"""Generate a comma-separated list of all 'page tools' links,
	such as 'View Source' and alternate directory views, that are
	applicable to the current page."""
	res = []
	for pt in ptools:
		res.append(pt(context))
	res = [x for x in res if x]
	return ', '.join (res)
register("pagetools", pagetools)

# The 'last modified' time that we display here is the last modified time
# of the *page file*, not the last modified time that will be splurted out
# as part of the HTTP response, because this is a lot more useful (if less
# show-offy).
# We use the ctime instead of the mtime for obscure reasons having to do
# with editing blog entries without changing their modtime.
def lastmod(context):
	"""Display the page's last modification time, if it has one. (This
	is not the same as the last-modified time that the HTTP response
	will have, which is taken from *all* of the pieces that contribute
	to displaying the page, including all templates.)"""
	# Not displayed for directories except in the normal view.
	if context.page.type == "dir" and context.view != "normal":
		return ''
	ts = context.page.timestamp
	if ts is not None and ts > 0:
		return time.asctime(time.localtime(ts))
	else:
		return ''
register("lastmodified", lastmod)

def lastctime(context):
	"""Display the page's last change time, if it has one. The change
	time is taken from the inode ctime."""
	ts = context.page.modstamp
	if ts > 0:
		return time.asctime(time.localtime(ts))
	else:
		return ''
register("lastchangetime", lastctime)

def readmore(context):
	"""Generate a 'Read more' link to this page."""
	return '<a href="%s">Read more &raquo;</a>' % \
	       context.url(context.page.me())
register("readmore", readmore)
