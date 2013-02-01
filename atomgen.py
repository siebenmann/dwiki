#
# Generate an Atom format feed for some portion of the dwiki hierarchy.
#
# See http://www.intertwingly.net/wiki/pie/FrontPage
# http://www.atomenabled.org/developers/syndication/atom-format-spec.php
#
# Atom feeds are generated from special templates using a view that
# knows that the result is not text/html. The renderers only have
# to worry about variable portions of the feed.
#
# See also http://www.xml.com/pub/a/2004/04/14/atomwiki.html
# Also http://feedvalidator.org/
# Also http://diveintomark.org/, for a parsing library.
#
# The complication with XML is that it requires escaping pretty
# much everything in the text, which fits badly with reusing
# standard renderers (which do not).
#
# Open issue: the live standard calls for <updated>...</updated>,
# and the validator doesn't. Right now I have all of updated,
# modified, and issued in there. (The validator can tentatively
# bite me.)
#
# Atom feed readers that intuit the port for us, so far:
# - liferea, NetNewsWire (OSX) & NNW Lite.
#
# Charset issues: see http://skew.org/xml/tutorial/
# Not only do I need UTF-8, but I need 'UTF-8 stripped of bad characters'.
# Or I need to throw errors on bad characters. Or just let the user blow
# their foot off. Or ... something.

import time
import urllib

import utils
import htmlrends, httputil, pageranges, wikirend, template
import comments
import views

# Atom is served as application/atom+xml.
atomCType = "application/atom+xml"

# This is arbitrary.
defCutoff = 100
def get_cutpoint(context):
	if 'atomfeed-display-howmany' in context:
		return context['atomfeed-display-howmany']
	else:
		return defCutoff

defFeedMax = False
def get_feedmax(context):
	if 'feed-max-size' not in context:
		return defFeedMax
	if 'feed-max-size-ips' not in context:
		return context['feed-max-size']
	sip = context['remote-ip']
	fmax = context['feed-max-size']
	if httputil.matchIP(sip, context['feed-max-size-ips']):
			return fmax
	return defFeedMax

# The minimum file timestamp to be included in the feed.
# This is somewhat of a hack feature, since it uses a Unix timestamp.
def get_cuttime(context):
	if 'feed-start-time' not in context:
		return 0
	else:
		return context['feed-start-time']

# Joy, yet another time string format.
def atomtimestr(ts):
	return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts))

def feedtitle(context):
	"""Generate an Atom feed title for the current page."""
	title = context.getfirst("wikititle", "wikiname")
	if context.page.path != "":
		title = title + " :: " + context.page.path
	return httputil.quotehtml(title)
htmlrends.register("atom::feedtitle", feedtitle)

#
# I think this is another case like that of redirection: you can't
# supply an explicit port because everyone will fix it up for you
# and thereby *double* the port.
# How this works when people pass feeds around I have no <censored>
# idea. Hopefully they rewrite the URL.
# NOTE: this makes it dangerous for use in <id> ... </id> context.
# Fix later. Head hurts.
# Apparently feed readers that do this are buggy (it should be an
# absolute URI). The question is 'how common are they?', because
# liferea is at least one of them.
def atomurl(context):
	"""Generate the URL of this page in its normal view."""
	url = context.nurl(context.page)
	return context.web.uri_from_url(url, context)
	#return context.nuri(context.page)
htmlrends.register("atom::pageurl", atomurl)

# Generate a page ID. This is either atom::pageurl or something
# generated from 'atomfeed-tag' plus the page path. See
# http://diveintomark.org/archives/2004/05/28/howto-atom-id for
# a discussion of Atom IDs and also
# http://atomenabled.org/developers/syndication/.
# The page path is quoted; the tag is considered to not need quoting.
# As a bonus feature, we have 'atomfeed-tag-time'; if this is defined,
# any entries from before this time return atom::pageurl and only entries
# after it return real atom::pagetags.
def atomtag(context):
	"""Generate an Atom entry ID. If the _atomfeed-tag_
	configuration option is not defined, this is the same as
	atom::pageurl. If _atomfeed-tag_ is defined, the entry ID is
	<tag value>:/<page path>. If _atomfeed-tag-time_ is defined,
	only pages from after this time are given tag-based IDs; for
	pages before then, this is the same as atom::pageurl."""
	if 'atomfeed-tag' not in context:
		return atomurl(context)
	# if our special magic tag is configured, we return old-style
	# atom url IDs for entries before the cutoff time.
	if 'atomfeed-tag-time' in context and \
	   context['atomfeed-tag-time'] > context.page.timestamp:
		return atomurl(context)
	tag = context['atomfeed-tag']
	return "%s:/%s" % (tag, urllib.quote(context.page.path))
htmlrends.register("atom::pagetag", atomtag)

def atomfeedurl(context):
	"""Generate the URL of this page for the current feed."""
	url = context.url(context.page)
	return context.web.uri_from_url(url, context)
htmlrends.register("atom::feedurl", atomfeedurl)

def atomstamp(context):
	"""Generate an Atom timestamp for the current page."""
	ts = context.page.timestamp
	if ts <= 0:
		ts = time.time()
	return atomtimestr(ts)
htmlrends.register("atom::timestamp", atomstamp)

def atomctime(context):
	"""Generate an Atom timestamp for the current page based on its
	change time."""
	ts = context.page.modstamp
	if ts <= 0:
		ts = time.time()
	return atomtimestr(ts)
htmlrends.register("atom::modstamp", atomctime)

# We have this because the <update> entry is required to come
# *before* the <entry> entries, and we only know the most recent
# time after we generate all of the latter. And we are not
# generating them, stashing them, and then recreating them
# for you. Updated is when we generated this feed, and that's
# *right now*, baby.
#
# This causes people to pull the feed all the time, but it costs
# us less CPU time (I think), *especially* if we are restricted.
# At the moment I feel I have more bandwidth than CPU time, so.
def atomnow(context):
	"""Generate an Atom timestamp for right now."""
	__pychecker__ = "no-argsused"
	return atomtimestr(time.time())
htmlrends.register("atom::now", atomnow)

# Generate and cache the list of pages that we are going to
# process.
atom_cachekey = "atom:pagelist"
def _fillpages(context):
	r = context.getcache(atom_cachekey)
	if r is not None:
		return r
	if context.page.type != "dir":
		return []
	cutpoint = get_cutpoint(context)
	cuttime = get_cuttime(context)

	#dl = context.page.descendants(context)
	# We deliberately use this context routine because it uses the
	# disk cache (if that exists).
	dl = context.cache_page_children(context.page)

	# Force the generator to be expanded to a full list so we can use
	# .sort on it.
	dl = list(dl)
	utils.sort_timelist(dl)
	if not dl:
		context.setcache(atom_cachekey, [])
		return []

	res = []
	count = 0
	dupDict = {}
	for ent in dl:
		if count >= cutpoint:
			break
		# Drop pages older than our cut time.
		if ent[0] < cuttime:
			continue
		np = context.model.get_page(ent[1])
		# We explicitly don't check access permissions here,
		# because what to show for forbidden pages is a policy
		# decision that is inappropriate to make here.
		if np.is_util() or np.is_redirect() or not np.displayable():
			continue
		# Suppress duplicate pages; these might happen through,
		# eg, hardlinks. When this happens we put only the first
		# one encountered in the Atom feed. Our sorting process
		# means that this is the lexically first, which may not
		# actually be the same one that was in the *last* Atom
		# feed generation run, but that's life.
		# Tricky issue: we assume that all versions of the page
		# have the same access permissions. If they don't, this
		# may suppress readable pages in favour of earlier
		# unreadable ones.
		pageid = np.identity()
		if pageid in dupDict:
			continue
		else:
			dupDict[pageid] = True
		count += 1
		res.append(ent)
	context.setcache(atom_cachekey, res)
	return res

def atompages(context):
	"""Generate an Atom feed of the current directory and all its
	descendants (showing only the most recent so many entries, newest
	first). Each page is rendered through _syndication/atomentry.tmpl_,
	which should result in a valid Atom feed entry. Supports
	VirtualDirectory restrictions."""
	to = context.model.get_template("syndication/atomentry.tmpl")
	res = []
	sz = 0
	maxsz = get_feedmax(context)
	rootpath = context.page.me().path
	if rootpath == '':
		rprefl = 0
	else:
		rprefl = len(rootpath)+1
	for ts, path in _fillpages(context):
		np = context.model.get_page(path)
		nc = context.clone_to_page(np)
		nc.setvar('relname', path[rprefl:])
		rdir = np.curdir().path[rprefl:]
		if rdir:
			nc.setvar('reldir', rdir)
		t = template.Template(to).render(nc)
		sz += len(t)
		res.append(t)
		context.newtime(nc.modtime)
		# Update for directory parent timestamp too.
		pdir = np.parent()
		context.newtime(pdir.modstamp)
		# And look for size limits. Note that we may go over
		# them, because we allow one entry's worth of slop;
		# this simplifies some cases.
		if maxsz and sz >= maxsz:
			break
	return ''.join(res)
htmlrends.register("atom::pages", atompages)

def atompagestamp(context):
	"""Generate an Atom format timestamp for an Atom page feed for
	the current directory (and all its descendants)."""
	rl = _fillpages(context)
	if not rl:
		return atomnow(context)
	tl = []
	for ts, path in rl:
		np = context.model.get_page(path)
		pdir = np.parent()
		tl.append(np.modstamp)
		tl.append(pdir.modstamp)
	return atomtimestr(max(tl))
htmlrends.register("atom::recentpage", atompagestamp)

def pageterse(context):
	"""Generate wikitext:terse run through a HTML entity quoter,
	thus suitable for use in Atom feeds."""
	return httputil.quotehtml(wikirend.terserend(context))
#htmlrends.register("atom::pageterse", pageterse)
wikirend.registerCached("atom::pageterse", pageterse)

#
# Generate and cache the list of comments that we are going to
# process.
atom_comkey = "atom:commentlist"
def _fillcomments(context):
	r = context.getcache(atom_comkey)
	if r is not None:
		return r
	cutpoint = get_cutpoint(context)

	dl = context.model.comments_children(context.page.me())
	# Force the generator to be expanded to a full list, so we can
	# sort it.
	dl = list(dl)
	utils.sort_timelist(dl)
	if not dl:
		context.setcache(atom_comkey, [])
		return []

	# Virtualization of comments means that we restrict the pages
	# that the comments are on to be within the virtualization
	# range. We cannot simply use pageranges.filter_files() on
	# the comments list itself, because the timestamps in that
	# are the *comment* timestamps, not the *page* timestamps.
	filterComments = False
	filterD = {}
	if pageranges.is_restriction(context):
		filterComments = True
		for ts, p in context.page.descendants(context):
			filterD[p] = True

	res = []
	count = 0
	for ts, path, cname in dl:
		if count > cutpoint:
			break
		if filterComments and path not in filterD:
			continue
		np = context.model.get_page(path)
		# We drop entirely pages that can't be accessed with
		# the current (lack of) permissions, rather than
		# insert a message about denied content; this seems
		# better.
		if not np.displayable() or np.is_redirect() or \
		   not np.access_ok(context):
			continue
		c = context.model.get_comment(np, cname)
		if not c:
			continue
		count += 1
		res.append((ts, path, cname, c))
	context.setcache(atom_comkey, res)
	return res

def atomcomments(context):
	"""Generate an Atom feed of recent comments on or below the
	current page. Each comment is rendered through
	_syndication/atomcomment.tmpl_. Supports VirtualDirectory
	restrictions, which limit which pages the feed will include
	comments for."""
	to = context.model.get_template("syndication/atomcomment.tmpl")
	res = []
	sz = 0
	maxsz = get_feedmax(context)
	for ts, path, cname, c in _fillcomments(context):
		np = context.model.get_page(path)
		nc = context.clone_to_page(np)
		nc.setvar(comments.com_stash_var, c)
		nc.setvar(":comment:name", cname)
		nc.setvar("comment-ip", c.ip)
		nc.setvar("comment-user", c.user)
		t = template.Template(to).render(nc)
		sz += len(t)
		res.append(t)
		context.newtime(nc.modtime)
		# clip maximum feed entry size (this is approximate, since
		# we don't know how much prefix and postfix we have).
		if maxsz and sz >= maxsz:
			break
	return ''.join(res)
htmlrends.register("atom::comments", atomcomments)

def atomcommentstamp(context):
	"""Generate an Atom format timestamp for the most recent comment
	that will be displayed in a comment syndication feed."""
	r = _fillcomments(context)
	if r:
		return atomtimestr(r[0][3].time)
	else:
		return atomnow(context)
htmlrends.register("atom::recentcomment", atomcommentstamp)

def atomcomment(context):
	"""Display the current comment in a way suitable for inclusion in
	an Atom feed."""
	if comments.com_stash_var not in context:
		return ''
	c = context[comments.com_stash_var]
	res = comments.show_comment(c.data, context, wikirend.ABSLINKS)
	context.newtime(c.time)
	return httputil.quotehtml(res)
htmlrends.register("atom::comment", atomcomment)

def commentstamp(context):
	"""Generate an Atom feed format timestamp for the current comment."""
	if comments.com_stash_var not in context:
		return ''
	c = context[comments.com_stash_var]
	return atomtimestr(c.time)
htmlrends.register("atom::commentstamp", commentstamp)

def commenturl(context):
	"""Generate the URL for the current comment."""
	if comments.com_stash_var not in context:
		return ''
	c = context[comments.com_stash_var]
	url = context.uri(context.page, context.comment_view())
	url += '#%s' % comments.anchor_for(c)
	return url
htmlrends.register("atom::commenturl", commenturl)

# Note that this is not a resolvable URL (the fragment isn't valid),
# *but* it is a *unique* one. However, it changes based on different
# hostnames used. Sigh. We can't goddamn win, apparently.
# The old default scheme of
#	tag:${wikiname}:${page}:${:comment:name}
# isn't necessarily global, and doesn't pass theoretical tag
# validation because a) it needs a ',1970-01-01' there, and
# b) it theoretically should be a domain and c) feedvalidator
# gets in a snit if the wikiname is not in all lower case.
#
# However, the hostname switching is no worse than regular
# atom feeds, for which the entry ID has always been the
# page's URL.
def commentid(context):
	"""Generate a hopefully unique ID for the current comment."""
	if comments.com_stash_var not in context:
		return ''
	url = context.nuri(context.page)
	url += '#%s' % context[':comment:name']
	return url
htmlrends.register("atom::commentid", commentid)

def hasatomfeed(context):
	return context.page.type == "dir" or \
	       context.page.path == context.wiki_root()

# Return the true page for an atom feed on a virtual directory. This
# is only different from the vdir if atomfeed-virt-only-* stuff is
# set; if it's set and the vdir restriction type is not one of the
# listed allowed ones, we return the true directory instead of the
# virtual directory.
# only_in is true if we should only take restrictions from
# atomfeed-virt-only-in, instead of starting with a-f-only-adv.
# This is used for enforcing actual restrictions on what feeds exist
# instead of just which ones are advertised.
def true_atom_page(context, only_in = False):
	if not context.page.virtual() or \
	   not pageranges.is_restriction(context) or \
	   not ('atomfeed-virt-only-in' in context or \
		'atomfeed-virt-only-adv' in context):
		# We must use .curdir() here because hasatomfeed() is
		# true for the root page as well as directories.
		return context.page.curdir()
	# tricky: .curdir() is not necessary now since virtual pages are
	# always directories.
	rt = pageranges.restriction(context)
	# If we are called with only_in True, a-v-only-in is known to exist.
	if only_in:
		atypes = context['atomfeed-virt-only-in']
	else:
		atypes = context.get('atomfeed-virt-only-adv',
				     context.get('atomfeed-virt-only-in', None))
	if rt in atypes or \
	   (rt in ('year', 'month', 'day') and 'calendar' in atypes):
		return context.page
	return context.page.me()

# Atom view of a directory or a page.
def atomfeed(context):
	"""Generate a link to the Atom feed for the current page if
	the current page is a directory or the wiki root."""
	if not hasatomfeed(context):
		return ''
	curl = context.url(true_atom_page(context), "atom")
	return htmlrends.makelink("Recent Pages", curl, True, atomCType)
htmlrends.register("atom::dirfeed", atomfeed)

def atomcommentfeed(context):
	"""Generate a link to the Atom comments feed for the current
	page, if comments are turned on."""
	if not context.model.comments_on():
		return ''
	page = context.page.me()
	# Special bonus hack.
	if page.path == context.wiki_root():
		page = page.parent()
	# .comments_on() can return True for pages no one can ever
	# comment on because the access restrictions to them are
	# impossible to pass. However, this is a lesser evil; it
	# just generates useless Atom feeds.
	# By using '!= "dir"' we automatically exclude bad pages
	# and so on from generating that Atom comment link.
	if page.type != "dir" and not page.comments_on(context):
		return ''
	curl = context.url(page, 'atomcomments')
	return htmlrends.makelink("Recent Comments", curl, True, atomCType)
htmlrends.register("atom::commentfeed", atomcommentfeed)

atomfeeds = (atomfeed, atomcommentfeed, )
def atomtools(context):
	"""Generate a comma-separated list of all Atom feed links, that
	are applicable for the current page."""
	res = []
	for ft in atomfeeds:
		res.append(ft(context))
	res = [x for x in res if x]
	return ', '.join(res)
htmlrends.register("atom::feeds", atomtools)

# This generates things suitable for autodiscovery in the <head>
# section.
def gendisclink(url):
	return '<link rel="alternate" type="%s" href="%s">' % (atomCType, url)

def atomdisc(context):
	"""Generate a suitable Atom feed autodiscovery _<link>_ string,
	suitable for inclusion in the _<head>_ section. Generates nothing
	if there is no Atom recent changes feed."""
	if hasatomfeed(context):
		return gendisclink(context.url(true_atom_page(context), "atom"))
	# For a page in a blogdir view, we generate an autodiscovery link
	# for the blog's top level feed. I think this is probably the
	# most useful thing to do in general, since it lets people who just
	# landed on a blog page immediately grab a full feed.
	# This would clash if we ever wanted to do comments, but ennh.
	(pv, vdir) = context.pref_view_and_dir(context.page.curdir())
	if pv != 'blog':
		return ''
	return gendisclink(context.url(vdir, "atom"))
htmlrends.register("atom::autodisc", atomdisc)

# ---
# Register the 'atom' and 'atomcomments' views.

# Atom views are just like template views, except that they can be
# used on the root directory and that their content-type is
# application/atom+xml, not text/html.
class AtomView(views.AltType):
	content_type = atomCType

#
# If the atomfeed-virt-only-in directive is set, we treat requests for
# disallowed atom feeds as some sort of problem. To be friendly, a
# disallowed request for a 'latest/' or 'range/' virtual directory is
# redirected to the base page's feed. Other requests produce 404's.
# This requires hooking into both page_ok() (for the 404's) and
# redirect_page() (for the redirections).
class RestrictedAtomView(AtomView):
	def _restrictable(self, optlist):
		if 'atomfeed-virt-only-in' not in self.context or \
		   not self.page.virtual() or \
		   not pageranges.is_restriction(self.context) or \
		   pageranges.restriction(self.context) in optlist:
			return False
		return True

	def page_ok(self):
		r = super(AtomView, self).page_ok()
		if not r or not self._restrictable(('latest', 'range')):
			return r
		tp = true_atom_page(self.context, True)
		if tp != self.page:
			self.error("nopage")
			return False
		return True

	def redirect_page(self):
		r = super(AtomView, self).redirect_page()
		if r or not self._restrictable(()):
			return r
		tp = true_atom_page(self.context, True)
		if tp == self.page:
			return False
		self.response.redirect(self.context.uri(tp, self.view))
		return True

# An atom view cannot be applied to a file, only a directory.
# Atom comments can be applied to anything.
views.register('atom', RestrictedAtomView, onDir = True, onFile = False)
views.register('atomcomments', AtomView, onDir = True)
