#
# Wikitext macros.

import time, re

import utils, search, httputil
import htmlrends, comments

# This exception is raised to signal that we want the rendering to
# return nothing; it is used by access restrictions to abort HTML
# generation. I generally assume that the templating system is
# going to notice this and do something to show a nice message,
# but even without that no one's seeing access-restricted text
# but the people who should be.
# Wiki text rendering arranges to guarantee that even a completely
# empty but properly rendered block of wikitext will still have
# some text in it by surrounding it with a big <div>.
class ReturnNothing(Exception):
	pass

# Macros are registered. This code is officially too simple to bother
# making generic.
reg_macros = {}
def register(name, callable):
	reg_macros[name] = callable
def get_macro(name):
	return reg_macros.get(name, None)
def all_macros():
	return reg_macros.keys()

# Code smell?
text_macros = []
macros_acount = {}
def register_text(name, callable, acount = 0):
	text_macros.append(name)
	if acount:
		macros_acount[name] = acount
	register(name, callable)
def register_acount(name, callable, acount):
	macros_acount[name] = acount
	register(name, callable)
def text_macro(name):
	return name in text_macros
def all_text_macros():
	return text_macros[:]
def arg_count(name):
	return macros_acount.get(name, 0)

# Processing notes
proc_notes = {}
procnotes_acount = {}
def register_pnote(name, callable, acount = 0):
	proc_notes[name] = callable
	procnotes_acount[name] = acount
def get_pnote(name):
	return proc_notes.get(name, None)
def all_pnotes():
	return proc_notes.keys()
def pnote_args(name):
	return procnotes_acount[name]

# So I can keep them straight, here's a way of dumping all of the
# available wikitext macros and/or renderers.
def enumerateall(rend, args):
	"""Enumerate all of the first argument, which must be
	'macros', 'processnotes' 'renderers', or 'textmacros' as a
	comma-separated list. The short form version of DocAll."""
	if not args or args[0] not in ('renderers', 'macros', 'processnotes',
				       'textmacros'):
		return False
	rend.markComplex()
	if args[0] == 'macros':
		rl = all_macros()
	elif args[0] == 'processnotes':
		rl = all_pnotes()
	elif args[0] == 'textmacros':
		rl = all_text_macros()
	else:
		rl = htmlrends.all_renderers()
	if rl:
		rl.sort()
		rend.text(', '.join(rl), "none")
	rend.ctx.unrel_time()
	return True
register("EnumerateAll", enumerateall)

def doc_all(rend, args):
	"""Enumerate all of the first argument (must be 'macros',
	'processnotes', 'renderers', or 'textmacros') with their
	documentation, if any, as a real HTML list.  (In other words,
	you're reading its output.)"""
	if not args or args[0] not in ('renderers', 'macros', 'processnotes',
				       'textmacros'):
		return False
	rend.markComplex()
	if args[0] == 'macros':
		lfunc, gfunc = all_macros, get_macro
	elif args[0] == 'processnotes':
		lfunc, gfunc = all_pnotes, get_pnote
	elif args[0] == 'textmacros':
		lfunc, gfunc = all_text_macros, get_macro
	else:
		lfunc, gfunc = htmlrends.all_renderers, htmlrends.get_renderer
	flist = lfunc()
	flist.sort()
	# Unlikely, but ...
	if not flist:
		return ''
	# Force non-striped:
	rend.useLists = True
	def _lif(fname):
		func = gfunc(fname)
		if hasattr(func, '__doc__') and func.__doc__:
			dstr = func.__doc__
		else:
			dstr = "(No documentation yet)"
		rend.addPiece("<code>%s</code>: " % fname)
		rend.text(dstr)
	rend.macro_list(_lif, None, flist)
	rend.ctx.unrel_time()
	return True
register("DocAll", doc_all)

# plist is a list of (page, name to display for page) things.
def genpagelist(rend, plist, view):
	def _format((p, pn)):
		rend.link_to_page(p, pn, view)
	rend.macro_list(_format, _format, plist)
def pagelist_names(rend, plist, view = None):
	genpagelist(rend, zip(plist, utils.yield_names(plist)), view)
def pagelist_paths(rend, plist, view = None):
	genpagelist(rend, zip(plist, plist), view)

# Note that this assumes that 'base' actually *is* the base.
def pagelist_paths_rel(rend, plist, base, view = None):
	if base[-1] != '/':
		base = base + '/'
	def _relpath(plist):
		l = len(base)
		for path in plist:
			if path.startswith(base):
				path = path[l:]
			if path:
				yield path
	genpagelist(rend, zip(plist, _relpath(plist)), view)

# Usage: reduce(common_pref_reduce, file-list, None)
# Results in either None or the common prefix (possibly '') of everything
# that didn't start with a '-' in the file-list.
def common_pref_reduce(a, b):
	if b[0] == '-':
		return a
	if a is None:
		return b
	return utils.common_prefix(a, b)

# Does a file match against a series of positive/negative path prefixes?
# Uses the same algorithm we'll later see in scanauthargs.
def file_matches_args(f, args):
	def _ispathprefix(a, b):
		return a == b or a.startswith(b+'/')
	matched = False
	all_negative = True
	for a in args:
		if a[0] == '-' and _ispathprefix(f, a[1:]):
			return False
		elif a[0] == '-':
			pass
		elif _ispathprefix(f, a):
			matched = True
		else:
			all_negative = False
	return (matched or all_negative)

defCutoff = 50
def recentArgsFixer(rend, args):
	cutoff = defCutoff
	if len(args) > 0:
		try:
			cutoff = int(args[0])
		except ValueError:
			pass

	# Fix up '.' and '/' in the args.
	for i in range(1, len(args)):
		if args[i] == '.':
			args[i] = rend.ctx.page.me().curdir().path
		elif args[i] == '-.':
			args[i] = '-'+rend.ctx.page.me().curdir().path
		elif args[i][0] == '/':
			args[i] = args[i][1:]
	return cutoff

def page_from_arg(rend, arg):
	if not arg:
		return rend.mod.get_page("")
	if arg == '.':
		return rend.ctx.page.curdir()
	elif arg[0] == '/':
		return rend.mod.get_page(arg[1:])
	else:
		return rend.mod.get_page(arg)

# First argument is the cutoff.
# Further arguments are currently ignored.
# If you gave us a cutoff, we throw in excluding redirects for
# free (because not doing so really irritates me).
def recentchanges(rend, args):
	"""List recently changed pages. First argument is how many to cut
	the list at, default 50; 0 means no limit, showing everything.
	Additional arguments are which directories to include or (with a dash
	at the start) to exclude from the list; you can use '.' to mean
	the current directory. To preserve the default limit, use a
	value for the first argument that is not a number.
	If we're Striped, list pages under their name not their full path."""

	rend.markComplex()
	
	# Fix up and handle arguments.
	cutoff = recentArgsFixer(rend, args)
	
	# For the especially perverse: if the starting path is not a
	# directory, we walk everything. Ha ha you lose.
	startpath = reduce(common_pref_reduce, args[1:], None)
	if startpath is None:
		startpath = ''
	else:
		npage = rend.mod.get_page(startpath)
		if npage.type != "dir":
			startpath = ""

	# Walk root downards, yes this bites.
	# It does give us the entire page list.
	#rl = rend.mod.get_page(startpath).descendants(rend.ctx)
	rl = rend.ctx.cache_page_children(rend.mod.get_page(startpath))
	if len(args) > 1:
		rl = [x for x in rl if file_matches_args(x[1], args[1:])]
	rl = list(rl)
	utils.sort_timelist(rl)

	# We'll show them all if you *really* want. After all,
	# we already took the hit to generate all the data.
	if cutoff > 0:
		nrl = []
		while rl and len(nrl) < cutoff:
			t = rl.pop(0)
			tp = rend.mod.get_page(t[1])
			if not tp.is_redirect():
				nrl.append(t)
		rl = nrl

	if rl:
		# Update the timestamp, to show off:
		rend.ctx.newtime(rl[0][0])
		rl = [z[1] for z in rl]

		# We list with full names unless we're in short display
		# more.
		if rend.useLists:
			pagelist_paths(rend, rl)
		else:
			pagelist_names(rend, rl)
	elif not rend.useLists:
		rend.addPiece("(none)")
	return True
register("RecentChanges", recentchanges)

# Just like RecentChanges, except we have no size limit and we
# alpha-sort. Instead, if we have arguments we restrict it to
# things that start with our arguments, whether in the full
# path or the page name.
def allpages(rend, args):
	"""List all pages. Arguments are prefixes of page paths and page
	names to restrict the list to. If you simply want to list all
	pages under a particular directory, you should use _AllPagesUnder_
	instead; it is more efficient (and more aesthetic)."""
	rend.markComplex()
	#rl = rend.mod.get_page("").descendants(rend.ctx)
	# The need to get the DWiki's entire page list is wince-inducing
	# but is the best I can do with this interface. Use AllPagesUnder
	# instead if you just want all pages under something.
	rl = rend.ctx.cache_page_children(rend.mod.get_page(""))

	# I am not obsessive enough to sort once to timestamp
	# order to see the most recent time, then a second time
	# in alpha order.
	rend.ctx.unrel_time()

	# Discard loss-leaders, if any.
	if args:
		r2 = []
		for ts, path in rl:
			pname = path.split("/")[-1]
			for a in args:
				if a and a[0] == '/' and path.startswith(a[1:]):
					r2.append(path)
					break
				if path.startswith(a) or \
				   pname.startswith(a):
					r2.append(path)
					break
		rl = r2
	else:
		rl = [z[1] for z in rl]
	if rl:
		rl.sort()
		pagelist_paths(rend, rl)
	return True
register("AllPages", allpages)

def allpagesunder(rend, args):
	"""List all pages under a particular directory, in alphabetical
	order.  Page names are shown relative to this directory (eg 'fred'
	instead of 'blog/fred' if blog is the directory). If there is no
	argument, the directory is the current directory of this page; if
	there is a single argument, it is the directory."""
	if len(args) > 1:
		return False
	elif len(args) == 0:
		dp = rend.ctx.page.curdir()
	else:
		dp = page_from_arg(rend, args[0])
	if dp.type != "dir":
		return False
	
	rend.markComplex()
	# I am not obsessive enough to sort once to timestamp
	# order to see the most recent time, then a second time
	# in alpha order.
	rend.ctx.unrel_time()

	# This always returns a list.
	rl = rend.ctx.cache_page_children(dp)
	rp = [z[1] for z in rl]
	if rp:
		rp.sort()
		pagelist_paths_rel(rend, rp, dp.path)
	return True
register("AllPagesUnder", allpagesunder)

# Like link_to_tm, but for a directory page instead of the current page.
# This is required for table_page_titles because the page we are
# rendering is not a directory.
def link_to_tm_root(context, dp, tm, plain = None):
	suf = "%d/%02d/%02d" % (tm.tm_year, tm.tm_mon, tm.tm_mday)
	if not plain:
		plain = suf
	page = context.model.get_virtual_page(dp, suf)
	return htmlrends.makelink(plain, context.url(page))

# Render a list of pages with their titles, as with blog::titles.
# The page list is in the desired order already. We may or may not
# include dates.
# TODO: this duplication is code smell.
def table_page_titles(rend, dp, plist, dodates):
	# Building a table is unfortunately much more complicated
	# than a <dl> would be, because we have to use <br> to separate
	# multiple entries for the same day instead of <td>, which means
	# that we have to keep track of when we need to generate one and
	# so on.
	rl = ['<table class="blogtitles">\n',]
	lday = None
	rootpath = dp.path
	# Rather than directly use a wikirend routine by importing
	# it, we indirect through the renderer registration. Since
	# either way we know a wikirend name, I figure this is no
	# worse.
	rfunc = htmlrends.get_renderer("wikitext:title:nolinks")
	for path in plist:
		# FIXME: this duplication is code smell.
		np = rend.mod.get_page(path)
		ts = np.timestamp

		# Do we need to generate a new row for a new day?
		# Our basic running state is that we are always in
		# a <td> for page links (except right at the start),
		# so we must close it off et cetera and then reopen
		# it.
		# This only applies if we are doing dates.
		t = time.localtime(ts)
		plain = "%d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
		if dodates and plain != lday:
			if lday:
				# Not first entry ever, so close off
				# the last day table row.
				rl.append("\n</td> </tr>\n")
			rl.append("<tr> <td> %s: </td> <td>\n" % \
				  link_to_tm_root(rend.ctx, dp, t, plain))
			lday = plain
		elif not dodates:
			# lday being non-None is our signal that we aren't
			# on the first row.
			if lday:
				rl.append(" </td> </tr>\n")
			rl.append("<tr> <td> ")
			lday = "yes"
		else:
			# If we are the second or later entry for a
			# given day, we must put a <br> between ourselves
			# and the previous entry.
			rl.append("<br>\n")

		# As usual, we must work in a new context.
		nc = rend.ctx.clone_to_page(np)
		ltitle = rfunc(nc)
		if not ltitle:
			ltitle = httputil.quotehtml(path[len(rootpath)+1:])
		# We can't use htmlrends.makelink() because that would
		# quote the live HTML in real titles.
		rl.append('    <a href="%s">%s</a>' % \
			  (rend.ctx.nurl(np), ltitle))
	# Done all; close off the <table>
	rl.append('\n</td></tr></table>\n')
	return ''.join(rl)

def gen_slug_re(wlist):
	# DWiki supports names like, well, 'DWiki', and in this case we
	# want to match if we're given 'Wiki'. Partly this is from Chris's
	# common usage, where leading caps have become common in titles.
	# We match all words using brute force, ie '|'.
	try:
		return re.compile(r"(?:\b|[-a-z0-9A-Z])(%s)(?:\b|[-A-Z])" % "|".join(wlist))
	except re.error:
		# There's not much we can do here except punt.
		return None

# TODO: Should we use re.escape() on the words? Not doing so allows
# for certain sorts of useful tricks, like 'match at end of page
# name', but perhaps that is too tricky, and we implicitly escape
# other searching (it's done in search.py). Using re.escape would
# also avoid running into exceptions during re building.
def matchingpagesunder(rend, args):
	"""List all pages under a particular directory if their page
	name contains a word in the provided word list. Usage is
	_!{{MatchingPagesUnder:directory:match ....:option ...:exclude ...}}_;
	the third and fourth arguments are optional and may be blank or
	omitted.
	Page names are shown relative to the directory (eg 'fred'
	instead of 'blog/fred' if blog is the directory). By default,
	pages are shown in reverse chronological order. The optional
	third argument sets various options, and may include '_c[hron]_'
	for the default reverse chronological order, '_+c[hron]_' for
	forward chronological order, '_a[lpha]_' for alpbahetical order
	based on the page name, '_t[able]_' for a table display similar
	to what _TitleIndex_ shows, and '_u[tilstoo]_' to include utility
	pages in the results.  Without the '_t_' option, this may be
	used with _PTitles_ or _Striped_, although use with _PTitles_
	doesn't change the alphabetical sort to use the titles; it is
	still based on page names.
	"""
	if len(args) < 2 or len(args) > 4:
		return False

	dp = page_from_arg(rend, args[0])
	if dp.type != "dir":
		return False

	# We might wind up with only whitespace in a perverse situation.
	# If so, reject things now.
	alist = args[1].split()
	if not alist:
		return False
	rexp = gen_slug_re(alist)
	if rexp is None:
		return False

	# Scan options, if any.
	# TODO: this is kind of messy.
	chronol = True
	rever = False
	dotbl = False
	inclutil = False
	if len(args) >= 3:
		olist = args[2].split()
		for i in olist:
			if i == 'c' or i == 'chron':
				chronol = True
				rever = False
			elif i == '+c' or i == '+chron':
				chronol = True
				rever = True
			elif i == 'a' or i == 'alpha':
				chronol = False
				rever = False
			elif i == 't' or i == 'table':
				dotbl = True
			elif i == 'u' or i == 'utilstoo':
				inclutil = True
			else:
				return False

	# Generate the exclusion list, if one is supplied.
	excl = None
	if len(args) >= 4:
		elist = args[3].split()
		if elist:
			excl = gen_slug_re(elist)
			if excl is None:
				return False

	rend.markComplex()
	# We don't try to set a timestamp, because there are too many
	# variables about what's included here.
	rend.ctx.unrel_time()

	# This always returns a list.
	rl = rend.ctx.cache_page_children(dp)
	# Winnow down the list to things that match, then sort the
	# result if called for.
	rp = [z for z in rl if rexp.search(z[1])]
	if excl is not None:
		rp = [z for z in rp if not excl.search(z[1])]
	if chronol:
		utils.sort_timelist(rp)
		r2 = [z[1] for z in rp]
		if rever:
			r2.reverse()
	else:
		# Alphabetical sorting cannot be reversed.
		r2 = [z[1] for z in rp]
		r2.sort()

	# Exclude alias and redirect pages, for fuzzy reasons, as
	# well as utility pages and pages that we can't access.
	# This is definitely tuned to the intended uses of this macro.
	# These checks should be okay because we assume that our page
	# list is generally small.
	# TODO: Should we exclude ourself?
	def _shouldshow(path):
		np = rend.mod.get_page(path)
		return np.realpage() and \
			(inclutil or not np.is_util()) and \
			np.access_ok(rend.ctx)
	r2 = [z for z in r2 if _shouldshow(z)]
	if r2:
		if dotbl:
			res = table_page_titles(rend, dp, r2, chronol)
			if not res:
				return False
			rend.addPiece(res)
		elif rend.useLists and dp.path:
			pagelist_paths_rel(rend, r2, dp.path)
		elif rend.useLists:
			pagelist_paths(rend, r2)
		else:
			pagelist_names(rend, r2)
	return True
register("MatchingPagesUnder", matchingpagesunder)

# Search for references to thing(s)
def listrefs(rend, args):
	"""List pages with references to one of the arguments, or where
	one of the arguments is a word in the page name. This is an
	expensive operation in a DWiki of any decent size, since it must
	search through all pages."""
	# We clearly have to be looking for something(s)
	if not args:
		return False
	rend.markComplex()
	hitlist = search.search_pages(rend.ctx, args)
	if not hitlist:
		return True
	# We display in alpha-sorted order.
	hitlist.sort()
	pagelist_paths(rend, hitlist)
	return True
register("ListRefs", listrefs)

def get_sub_macro(rend, args):
	if not args:
		return None
	mf = get_macro(args[0])
	if not mf:
		return None
	# We must check to see if it's an allowed macro, because
	# because otherwise people could accidentally execute macros
	# in contexts they shouldn't be run. We don't have to duplicate
	# the NOMACROS check because if NOMACROS was on we wouldn't be
	# in here to start with.
	if not rend.allowedmacro(args[0]):
		return None
	return mf

# Render a macro with lists striped, not as straight lists.
def striped(rend, args):
	"""Make another macro generate lists of pages as a comma-separated
	line, instead of the real list it would normally use. Invoked as
	_!{{Striped:<macro>[:arg:arg...]}}_."""
	mf = get_sub_macro(rend, args)
	if mf is None:
		return False
	rend.useLists = False
	res = mf(rend, args[1:])
	rend.useLists = True
	return res
register("Striped", striped)

def pagetitled(rend, args):
	"""Make another macro generate lists of pages using the titles
	of the pages (if possible), instead of the names of the pages.
	Invoked as _!{{PTitles:<macro>[:arg:arg...]}}_."""
	mf = get_sub_macro(rend, args)
	if mf is None:
		return False
	rend.usePageTitles = True
	res = mf(rend, args[1:])
	rend.usePageTitles = False
	return res
register("PTitles", pagetitled)

# Produce a listing of everything in the current directory.
# With an argument, which must be 'file' or 'directory',
# list just things of that type.
def listdir(rend, args):
	"""List what's in the current directory. An argument restricts it
	to either files ('files') or subdirectories ('directory')."""
	restrict = None
	if args:
		if args[0] not in ('file', 'directory'):
			return False
		restrict = args[0]
		if restrict == 'directory':
			restrict = 'dir'
	fc = rend.ctx.page.curdir().children(restrict)
	if fc:
		pagelist_names(rend, [z.path for z in fc])
	return True
register("ListDir", listdir)

# Parse the arguments for an authentication geegaw, returning
# True / False if they pass.
def scanauthargs(args, uent):
	if not args:
		return True
	ul = [uent.user]
	ul.extend(uent.groups)
	matched = False
	all_negative = True
	for a in args:
		# 'not this thing' and 'are this thing' means we
		# fail instantly.
		if a[0] == '-' and a[1:] in ul:
			return False
		elif a[0] == '-':
			pass
		elif a in ul:
			matched = True
		else:
			all_negative = False
	# We didn't fail a negative check. If we met a positive
	# check, or there were no positive checks, we are accepted;
	# otherwise we failed.
	return (matched or all_negative)

# Authentication support.
# This aborts us to produce no text if the authentication fails.
# Without arguments, this restricts the wikitext to authenticated
# people. With arguments, it restricts it to just those people or
# groups; an argument that starts with a '-' is taken to be an
# exclusion. If there are only exclusions, we succeed if we are
# authenticated but not any of those people/groups.
def restricted(rend, args):
	"""Restrict a page to authenticated users. Arguments are which users
	or groups to allow access to or, with a dash at the front, to deny
	access to. If both allow and deny arguments are given, the viewing
	user must pass both tests. Restricted has no effect if the DWiki
	has no authentication configured."""
	# If the wiki has no authentication, we *succeed*.
	if not rend.mod.has_authentication():
		return True
	# Note that this page does indeed have restrictions.
	rend.addFeature("hasrestricted")
	# If we are not authenticated, we fail automatically.
	uent = rend.ctx.current_user()
	if not uent:
		raise ReturnNothing("no user")
	# See if we pass the arguments.
	if scanauthargs(args, uent):
		return True
	# Otherwise we have failed authentication.
	raise ReturnNothing("failed to authenticate")
register("Restricted", restricted)

# Enable comments on this wikitext by setting the 'comments'
# feature as a side effect. To comment, we must be authenticated
def cancomment(rend, args):
	"""Allow authenticated users to comment on a page. Arguments are
	users to allow or deny access to, as with the Restricted macro.
	A DWiki without authentication disallows comments, as no one is
	authenticated."""
	if not rend.mod.has_authentication():
		return True
	# It looks like this page is at least potentially commentable
	# on, so mark it as such with a feature.
	rend.addFeature("hascomments")
	# If given args, only people who pass them can comment.
	if args:
		uent = rend.ctx.current_user()
		if not uent:
			return True
		if not scanauthargs(args, uent):
			return True
	# Otherwise, great, we can comment.
	rend.addFeature("comments")
	return True
register("CanComment", cancomment)


#
# This is basically a backwards compatibility hack. CutShort is not
# actually a macro any more; instead it is a block level entity that
# vaguely acts like a macro in some contexts. The actual macro
# implementation is here a) for the help text and b) so that it can
# show up as a clear error if people use it in the wrong context.
def cutshort(rend, args):
	"""Cut off rendering a page right at that point in some
	contexts.  Optional arguments restrict this effect to the
	specified view(s); it's generally not useful to do this, but
	if you want to some values are _blog_, _blogdir_, or
	_atom_. Rendering as a full page can never be cut off.  An
	important note: _CutShort_ is not really a macro. You must put
	it at the start of a non-indented, non-nested line; it doesn't
	work anywhere else in text."""
	return False
register("CutShort", cutshort)
# Old text generated:
#	rend.addPiece('<p class="teaser"> [More available: ')
#	rend.makelink(rend.ctx.nurl(rend.ctx.page), rend.ctx.page.name)
#	rend.addPiece("] </p>")

# Pages with recent comments for them.
def rec_comment_pages(rend, args):
	"""List pages with recent comments. Arguments are the same as for
	RecentChanges."""
	rend.markComplex()
	cutoff = recentArgsFixer(rend, args)
	
	startpath = reduce(common_pref_reduce, args[1:], None)
	if startpath is None:
		startpath = ''

	spage = rend.mod.get_page(startpath)
	#cl = rend.mod.comments_children(spage)
	cl = comments.cached_comments_children(rend.ctx, spage)
	# There is no point checking cl, because it is always a generator.

	# Now we get to condense it from a list of recent comments
	# down to a list of pages with recent comments.
	d = {}
	fargs = args[1:]
	for com in cl:
		ppath = com[1]
		if ppath in d or \
		   (fargs and file_matches_args(ppath, fargs)):
			continue
		d[ppath] = com[0]
	cl = [(d[x], x) for x in d.keys()]
	utils.sort_timelist(cl)
	
	# Unlike RecentChanges, we know that these should be real pages.
	# (If they're not, we have problems.)
	if cutoff > 0 and cl:
		cl = cl[:cutoff]
	if cl:
		rend.ctx.newtime(cl[0][0])
		cl = [z[1] for z in cl]
		# We list with full names unless we're in short display
		# more.
		view = rend.ctx.comment_view()
		if rend.useLists:
			pagelist_paths(rend, cl, view)
		else:
			pagelist_names(rend, cl, view)
	elif not rend.useLists:
		rend.addPiece("(none)")
	return True
register("RecentCommentedPages", rec_comment_pages)

def recentcomments(rend, args):
	"""List recent comments. Arguments are the same as for
	RecentChanges. Use with _Striped_ is somewhat dubious."""
	rend.markComplex()
	cutoff = recentArgsFixer(rend, args)
	
	startpath = reduce(common_pref_reduce, args[1:], None)
	if startpath is None:
		startpath = ''

	spage = rend.mod.get_page(startpath)
	#cl = rend.mod.comments_children(spage)
	cl = comments.cached_comments_children(rend.ctx, spage)
	# There is no point checking cl, because it is always a generator.

	if len(args) > 1:
		fargs = args[1:]
		cl = [x for x in cl if file_matches_args(x[1], fargs)]
	cl = list(cl)
	utils.sort_timelist(cl)
	if cutoff > 0:
		cl = cl[:cutoff]
	if not cl:
		if not rend.useLists:
			rend.addPiece("(none)")
		return True

	view = rend.ctx.comment_view()
	rend.ctx.newtime(cl[0][0])

	def _cominfo(ppath, cname):
		npage = rend.mod.get_page(ppath)
		c = rend.mod.get_comment(npage, cname)
		if not c:
			return (None, None, None)
		url = rend.ctx.url(npage, view)
		ca = comments.anchor_for(c)
		url += '#%s' % ca
		return (c, npage, url, ca)

	def _lif((ts, ppath, cname)):
		(c, npage, url, ca) = _cominfo(ppath, cname)
		if not c:
			return
		rend.addPiece('<a href="%s">' % url)
		if c.username and not c.is_anon(rend.ctx):
			rend.text("%s (%s)" % (c.username, c.user), "none")
		elif not c.is_anon(rend.ctx):
			rend.text(c.user, "none")
		elif c.username:
			rend.text(c.username, "none")
		else:
			rend.text(c.ip, "none")
	
		tstr = time.strftime("%Y-%m-%d %H:%M",
				     time.localtime(c.time))
		rend.addPiece(" at "+tstr)
		rend.addPiece("</a>, on ")
		url2 = rend.ctx.url(npage, view)
		rend.makelink(url2, npage.name)
		rend.addPiece("\n")
	def _bf((ts, ppath, cname)):
		(c, npage, url, ca) = _cominfo(ppath, cname)
		if not c:
			return
		rend.addPiece('<a href="%s">' % url)
		rend.text(ca, "none")
		rend.addPiece("</a>")
	rend.macro_list(_lif, _bf, cl)
	return True
register("RecentComments", recentcomments)

# Show some context variables. Context variables are text-only.
safe_vars = ('wikiname', 'wikititle', 'server-name', 'pagedir',
	     'charset', )
# TODO: there should be a better way to generate the docstring
# here.
def showvars(rend, args):
	"""Insert the value of a DWiki configuration variable.  The
	argument is which variable to insert. Only a few variables may
	be displayed, currently _wikiname_, _wikititle_,
	_server-name_, _pagedir_, and _charset_."""
	if not args or len(args) != 1 or \
	   args[0] not in safe_vars:
		return False
	if  args[0] not in rend.ctx:
		rend.text("{no value set}", "none")
	else:
		rend.text(rend.ctx[args[0]], "none")
	return True
register("ShowCfgVar", showvars)

# This looks like it could recurse, but it doesn't (unless you are
# very perverse). The reason why not is that blog::titles specifically
# fetches titles and title-fetching is not general rendering; we should
# never reach the point in a page where we will re-render a {{TitleIndex}}
# macro unless you perversely put it in the first two lines of a file.
# Note that this problem is also shared by PTitles.
# TODO: this (like several other things) should close any open paragraph
# (killing font styles et al).
# That needs a new rend.addHTMLBlock() method or something.
def m_titleindex(rend, args):
	"""Insert a table of dates and entry titles (or relative paths
	for entries without titles), linking to entries and to the day
	pages. The table is in reverse chronological order. The single
	argument is the page hierarchy to do this for; if it is not
	specified, it is done for the current directory. The actual
	rendering is done by the blog::titleindex renderer."""
	if len(args) > 1:
		return False
	elif len(args) == 1:
		dp = page_from_arg(rend, args[0])
	else:
		dp = rend.ctx.page.curdir()
	if dp.type != "dir":
		return False
	rend.markComplex()
	nc = rend.ctx.clone_to_page(dp)
	rnd = htmlrends.get_renderer("blog::titles")
	res = rnd(nc)
	if not res:
		return False
	rend.addPiece(res)
	return True
register("TitleIndex", m_titleindex)

##
# This is an ugly escape because we are out of pleasant formatting
# options.
# See http://www.w3.org/TR/html401/
#
# Our support for <INS> and <DEL> is very primitive, since we can't
# add cite, datetime, or title attributes and we can't use them as
# block level elements.
#
text_styles = ('big', 'del', 'ins', 'small', 'strike', 'u', 'sup', 'sub', )
def showtext(rend, args):
	"""Style text with a particular HTML font style. The first argument
	is the HTML font style; the remainder are the text to be in that
	style. Valid styles are _big_, _del_, _ins_, _small_, _strike_,
	_sub_, _sup_, and _u_."""
	if len(args) < 2 or args[0] not in text_styles:
		return False
	txt = ':'.join(args[1:])
	rend.start_style(args[0])
	rend.set_style_barrier()
	rend.text(txt)
	rend.end_style(args[0])
	rend.clear_style_barrier()
	return True
register_text("ST", showtext, 2)

#known_ents = ('copy', 'reg', 'deg', 'para', 'hellip', 'trade',
#	      'ndash', 'mdash', 'lsquo', 'rsquo', 'sbquo', 'ldquo', 'rdquo',
#	      'bdquo', 'dagger', 'Dagger', 'prime',
#	      'euro', 'cent', 'pound', 'yen',
#	      'larr', 'uarr', 'rarr', 'darr', 'harr', 'crarr', )
import htmlentitydefs
known_ents = htmlentitydefs.name2codepoint.keys()

def showchar(rend, args):
	"""Insert a character entity. The character entity may be given as
	a decimal number or as a HTML 4.01 character entity name. See the
	_ShowCharEnts_ macro for how to display the list of known character
	entity names."""
	if len(args) != 1:
		return False
	cn = args[0]

	if cn in known_ents:
		rend.addPiece('&%s;' % cn)
		return True
	# It must otherwise be a character.
	try:
		if cn[0] == 'x':
			char = int(cn[1:], 16)
		else:
			char = int(args[0], 10)
	except ValueError:
		return False

	# Valid in our range, more or less?
	# This deliberately excludes all control characters, including
	# tab, CR, and LF, because using them in numeric entities is
	# somewhere between dubious and silly.
	if char < 32 or 127 <= char <= 159:
		return False
	rend.addPiece("&#%s;" % cn)
	return True
register_text("C", showchar)

def ciCmp(a, b):
	return cmp(a.lower(), b.lower()) or \
	       cmp(a, b)
def showcharents(rend, args):
	"""Show all the known character entities accepted by the _C_ macro
	as a real HTML list. Takes no arguments."""
	if len(args) != 0:
		return False
	ents = list(known_ents)
	#ents.sort(ciCmp)
	# We must sort twice to emulate ciCmp's behavior, since the second
	# case-independent sort will be stable.
	ents.sort()
	ents.sort(key=lambda x: x.lower())
	def _lif(e):
		rend.addPiece("%s: &%s;" % (e, e))
	def _bf(e):
		rend.addPiece("%s (&%s;)" % (e, e))
	rend.macro_list(_lif, _bf, ents)
	return True
register("ShowCharEnts", showcharents)

def safe_attr_text(txt):
	txt = txt.replace("&", "&amp;").replace('"', "&quot;")
	txt = txt.replace("\r\n", " ").replace("\n", " ")
	return txt

# Per the discussion at
# http://www.benmeadowcroft.com/webdev/articles/abbr-vs-acronym.shtml
# we use <abbr> instead of <acronym> because it's more general.
# Charmingly, IE doesn't support <abbr>. Get Firefox; I am angry.
def showabbr(rend, args):
	"""Generate an inline HTML <abbr> element. The first argument is
	the abbreviation and the following arguments are the expansion.
	Once the abbreviation has been used once, the expansion is optional."""
	if len(args) == 0:
		return False
	abbr = args[0]
	if len(args) == 1:
		if abbr not in rend.abbrCache:
			return False
		exp = rend.abbrCache[abbr]
	else:
		exp = ":".join(args[1:])
		exp = safe_attr_text(exp)
		rend.abbrCache[abbr] = exp
	rend.addPiece('<abbr title="%s">' % exp)
	rend.text(abbr, "fonts")
	rend.addPiece("</abbr>")
	return True
register_text("AB", showabbr, 2)

# Images. This is vaguely evil.
def showimage(rend, args):
	"""Generate an inline image. Usage is
	_!{{IMG:<loc> width height alt text ...}}_, where the height
	should normally be '_auto_', which set the image so that it will
	automatically scale down for the browser width in modern CSS
	environments. If the location is not absolute (http:, https:,
	or starts with a /) it is taken as a location relative to the
	DWiki _staticurl_ directory. The location cannot include spaces; %
	encode them if necessary. After the first time you use an image,
	specifying the width, height, and alt text is optional; if not
	specified, they default to the last values.  If the alt text
	contains '_ ||| _', it is split there to be alt text (before)
	and title text (afterwards). The title text is what browsers
	show when you hover over the image."""
	if len(args) != 1:
		return False
	sr = args[0].split(None, 3)
	loc = sr[0]
	if rend.is_absurl(loc) or loc[0] == '/':
		# absolute enough, do nothing.
		pass
	elif 'staticurl' not in rend.ctx:
		return False
	else:
		loc = rend.ctx['staticurl'] + "/" + loc

	# To save people work we remember image attributes after the
	# first time we see them.
	if len(sr) == 1:
		if loc not in rend.imgCache:
			return False
		(width, height, alt) = rend.imgCache[loc]
	elif len(sr) == 4:
		(_, width, height, alt) = sr
	else:
		return False

	# Width and height had better be valid as integers, and be
	# larger than zero.
	# Actually, HTML allows 'nn%', so what the hell, run free.
	# We can at least disallow 0 or negative integer widths.
	try:
		if int(width) <= 0 or int(height) <= 0:
			return False
	except ValueError:
		#return False
		pass

	# We only cache *after* validation.
	alt = safe_attr_text(alt)
	if len(sr) == 4:
		rend.imgCache[loc] = (width, height, alt)

	# The choice of ' ||| ' for the alt / title split is
	# arbitrary, but picked because it is unlikely to be sensible
	# in anything else.
	suf = ''
	if ' ||| ' in alt:
		res = alt.split(' ||| ', 1)
		if len(res) == 2:
			alt, title = res
			suf = ' title="%s"' % title
	# Render and dump.
	style = ""
	if height == "auto":
		style = ' style="max-width: 100%"'
	txt = '<img%s src="%s" width="%s" height="%s" alt="%s"%s>' % \
	      (style, rend.canon_url(loc), width, height, alt, suf)
	rend.addPiece(txt)
	return True
register_acount("IMG", showimage, 1)


##
# Processing notes change how future text is processed until they
# are cancelled.
def pn_disable(rend, args):
	"""Disable the text style that is the argument, so it will now
	render as literal text instead. Text styles that can be disabled
	are ((_)), ((*)), and ((~~))."""
	if args[0] not in ('_', '*', '~~'):
			return False
	rend.disableStyle(args[0])
	return True
def pn_enable(rend, args):
	"""Return text style handling to its normal state of affairs,
	with no text styles disabled. This takes no arguments."""
	__pychecker__ = "no-argsused"
	rend.enableStyles()
	return True
register_pnote("no", pn_disable, 1)
register_pnote("yes", pn_enable, 0)

def pn_sub(rend, args):
	"""From now on, the first argument is replaced with the second
	argument in ordinary text before text-level things like text
	styles and links are handled (so watch out for possible
	effects on URLs in ![[...]] and other things that appear in
	ordinary text but aren't text)."""
	rend.makeSub(args[0], args[1])
	return True
register_pnote("sub", pn_sub, 2)
def pn_wordsub(rend, args):
	"""This is like _sub_, but makes some attempt to only do the
	substitution where the first argument looks like a full word,
	not when it is just part of one."""
	rend.makeSub(args[0], args[1], True)
	return True
register_pnote("wordsub", pn_wordsub, 2)

def pn_literal(rend, args):
	"""Render the argument literally, in that no font, macro, or
	link substitutions take place."""
	#"""Do our best to render the argument literally in text, by
	#creating a substitution where it is mapped to ![[<itself>|]].
	#This is subject to all the frailities of _sub_."""
	#rend.makeSub(args[0], "[[%s|]]" % args[0])
	rend.add_stopword(args[0])
	return True
register_pnote("lit", pn_literal, 1)
def pn_noliteral(rend, args):
	"""Stop rendering the argument literally."""
	rend.clear_stopword(args[0])
register_pnote("unlit", pn_noliteral, 1)

def pn_endsub(rend, args):
	"""Stop doing text substitution for the argument."""
	rend.delSub(args[0])
	return True
register_pnote("unsub", pn_endsub, 1)
def pn_nosub(rend, args):
	"""Remove all current text substitutions and clears all
	current literals."""
	__pychecker__ = "no-argsused"
	rend.delAllSubs()
	rend.clear_stopwords()
	return True
register_pnote("nosubs", pn_nosub, 0)

def pn_prewrap(rend, args):
	"""Turn on word-wrapping in preformatted text or turn it back off
	again (which is the default state and the normal behavior of
	HTML's _<pre>_). The sole argument is either '_on_' or '_off_'.
	Word-wrapping preformatted text can look better in many cases
	because it avoids scrollbars and keeps the entire text visible."""
	if args[0] == "on":
		rend.setPreWrap(True)
	elif args[0] == "off":
		rend.setPreWrap(False)
	else:
		return False
	return True
register_pnote("prewrap", pn_prewrap, 1)
