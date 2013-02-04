#
# Support a 'blog' style view of a directory.
import time

import derrors, htmlrends, template, views
import pageranges
import httputil

def blogtime(context):
	"""Generate a YYYY-MM-DD HH:MM:SS timestamp of the current page."""
	ts = context.page.timestamp
	if ts:
		dstr = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
		return dstr
	else:
		return ''
htmlrends.register("blog::time", blogtime)

# Just year-month-day
def blogdate(context):
	"""Generates a YYYY-MM-DD timestamp of the current page."""
	ts = context.page.timestamp
	if ts:
		dstr = time.strftime("%Y-%m-%d", time.localtime(ts))
		return dstr
	else:
		return ''
htmlrends.register("blog::date", blogdate)

def blogtod(context):
	"""Generates a HH:MM:SS timestamp of the current page."""
	ts = context.page.timestamp
	if ts:
		dstr = time.strftime("%H:%M:%S", time.localtime(ts))
		return dstr
	else:
		return ''
htmlrends.register("blog::timeofday", blogtod)

# blogdate, but generated on a rolling basis; we don't generate it
# if we've already generated this string and it's in the context.
# (Except we store it in a parent object so it will persist across
# cloned contexts. Maybe we should introduce the idea of a parent
# context.)
# FIXME: really, we should.
rollvar = ":blog:entrystore"
def rollingdate(context):
	"""Inside a blog::blog or blog::blogdir rendering, generate a
	YYYY-MM-DD date stamp for the current page *if* this has changed
	from the last page; otherwise, generates nothing."""
	res = blogdate(context)
	if not res:
		return ''
	if rollvar in context and \
	   'date' in context[rollvar] and \
	   context[rollvar]['date'] == res:
		return ''
	else:
		if rollvar in context:
			context[rollvar]['date'] = res
		return res
htmlrends.register("blog::datemarker", rollingdate)

# This may have to grow more complicated in the future, but for now
# I laugh madly and sadly and say 'no, I think not'; we use Unix
# ownership.
def pageowner(context):
	"""Display the owner of the current page."""
	owner = context.page.owner()
	if owner:
		return owner
	else:
		return ''
htmlrends.register("blog::owner", pageowner)

#
# This is the complicated renderer. It renders all of the pages in
# a directory into a single (potentially horking big) blob, using
# a 'blog/blogdirpage.tmpl' template for each of them. Entries are sorted
# by modtime, newest first.
def blogdir(context):
	"""Generate a BlogDir rendering of the current directory:
	display all real pages in the current directory from most
	recent to oldest, rendering each with the template
	_blog/blogdirpage.tmpl_. Supports VirtualDirectory restrictions."""
	if context.page.type != "dir":
		return ''
	if not context.page.displayable():
		raise derrors.IntErr, "undisplayable directory page"
	dl = context.page.children("file")
	if not dl:
		return ''
	# Boil it down to just files, in modtime order.
	# (Technically this is real files: displayable, non-redirect.)
	dl = [z for z in dl if z.realpage() and not z.is_util()]
	# This directory might have nothing.
	if not dl:
		return ''

	# Apply restrictions, backwardly.
	# Because restrictions are designed to be hugely scalable, they
	# want the output we'd get from page.descendants().
	if pageranges.is_restriction(context):
		tl = pageranges.filter_files(context, [(z.timestamp, z.path)
						       for z in dl])
		if not tl:
			return ''
		dl = [context.model.get_page(z[1]) for z in tl]
	else:
		dl.sort(lambda x,y: cmp(y.timestamp, x.timestamp))

	# For each file, clone the context, set the current page to
	# it (!), and render it with the blogentry template.
	to = context.model.get_template("blog/blogdirpage.tmpl")
	context.setvar(rollvar, {})
	res = []
	for page in dl:
		# Note: we do not reset the view type, because we do
		# not render through the interface that cares; we go
		# straight to template.
		nc = context.clone_to_page(page)
		res.append(template.Template(to).render(nc))
		context.newtime(nc.modtime)
	return ''.join(res)
htmlrends.register("blog::blogdir", blogdir)

# Regular directory listing goes here for obscure reasons.
# Render a directory, with help from the model.
def directory(ctx):
	"""List the contents of the current directory, with links to each
	page and subdirectory. Supports VirtualDirectory restrictions, but
	always shows subdirectories."""
	res = []
	if ctx.page.type != "dir" or not ctx.page.displayable():
		return ''

	# Just in case:
	ctx.newtime(ctx.page.timestamp)
	dl = ctx.page.children()
	if not dl:
		return ''
	# Restrict the results if we've been asked to. This is complicated
	# by our need to preserve directories *always*, because we don't
	# know if they have any files within the restriction inside them.
	if pageranges.is_restriction(ctx):
		dirl = [z for z in dl if z.type == 'dir']
		tl = pageranges.filter_files(ctx, [(z.timestamp, z.path)
						   for z in dl
						   if z.type != 'dir'])
		if not tl and not dirl:
			return ''
		dl = [ctx.model.get_page(z[1]) for z in tl]
		dl.extend(dirl)
		dl.sort(lambda x,y: cmp(x.name, y.name))

	res.append("<ul>")
	for de in dl:
		res.append("\n<li> ")
		res.append(htmlrends.makelink(de.name, ctx.url(de)))
	res.append("\n</ul>\n")
	return ''.join(res)
htmlrends.register("listdir", directory)


#
# ---------------

# If we are not using a specific limit restriction or a day
# restriction and there is 'too much', clip us down and note
# this fact. We clip only at day boundaries, because when we
# drill down to the days we will show that many entries on
# a page anyways.
def clipDown(context, dl):
	if 'blog-display-howmany' in context:
		cutpoint = context['blog-display-howmany']
	else:
		cutpoint = TOPN_NUMBER
	if len(dl) > cutpoint and \
	   (not pageranges.is_restriction(context) or \
	    pageranges.restriction(context) in ('year', 'month')):
		t1 = time.localtime(dl[cutpoint-1][0])
		i = cutpoint
		while i < len(dl):
			t2 = time.localtime(dl[i][0])
			if t1.tm_mday != t2.tm_mday:
				break
			i += 1
		if i < len(dl):
			context.setvar(":blog:clippedrange", dl[i])
			context.setvar(":blog:clipsize", i)
			dl = dl[:i]
	return dl

# 'blog' support.
# This is like blogdir but with two differences:
# First, we look at all descendants, not just direct children.
# And second, unless we are restricted to days, ranges, or latest, we
# only show the top 'n' of the result.
TOPN_NUMBER = 10
def blogview(context):
	"""Generate a Blog rendering of the current directory: all
	descendant real pages, from most recent to oldest, possibly
	truncated at a day boundary if there's 'too many', and sets
	up information for blog navigation renderers. Each
	displayed page is rendered with the _blog/blogentry.tmpl_
	template. Supports VirtualDirectory restrictions."""
	if context.page.type != "dir":
		return ''
	if not context.page.displayable():
		raise derrors.IntErr, "undisplayable directory page"

	# This automatically applies restrictions.
	dl = context.cache_page_children(context.page)
	if not dl:
		return ''
	dl = clipDown(context, dl)

	# For each file, clone the context, set the current page to
	# it (!), and render it with the blogentry template.
	to = context.model.get_template("blog/blogentry.tmpl")

	# Set up our rolling storage. We can't hook directly onto
	# the context, because we drop the context after every
	# entry. So we write our own dictionary into the context.
	context.setvar(rollvar, {})
	res = []
	rootpath = context.page.me().path
	dupDict = {}
	for ts, path in dl:
		# Skip redirects and other magic files.
		# We go whole hog and drop anything that is not a real page,
		# which subsumes both redirect pages and non-displayable
		# pages. Since attempting to render a non-displayable page
		# is a fatal internal error, we must drop them before we
		# go to the template.
		np = context.model.get_page(path)
		if not np.realpage() or np.is_util():
			continue
		# Suppress multiple occurrences of the same page as
		# may happen with, eg, hardlinks. Note that this is
		# slightly questionable; macros mean that a file's
		# rendering output may depend on not just its contents
		# but its position in the file hierarchy. We don't
		# care.
		pageid = np.identity()
		if pageid in dupDict:
			continue
		else:
			dupDict[pageid] = True
		# Note: we do not reset the view type, because we do
		# not render through the interface that cares; we go
		# straight to template.
		nc = context.clone_to_page(np)
		nc.setvar('relname', path[len(rootpath)+1:])
		res.append(template.Template(to).render(nc))
		context.newtime(nc.modtime)
	return ''.join(res)
htmlrends.register("blog::blog", blogview)

def link_to_tm(context, tm, plain = None):
	suf = "%d/%02d/%02d" % (tm.tm_year, tm.tm_mon, tm.tm_mday)
	if not plain:
		plain = suf
	page = context.model.get_virtual_page(context.page.me(), suf)
	return htmlrends.makelink(plain, context.url(page))

def cutoff(context):
	"""With blog::blog, generates a 'see more' link to the date of
	the next entry if the display of pages has been	truncated; the
	text of the link is the target date. This renderer is somewhat
	misnamed."""
	if ":blog:clippedrange" not in context:
		return ''
	rv = context[":blog:clippedrange"]
	t = time.localtime(rv[0])
	# We could really use anchors here.
	return link_to_tm(context, t)
htmlrends.register("blog::seemore", cutoff)

def month_cutoff(context):
	"""With blog::blog, generate a 'see more' set of links for the
	month and the year of the next entry if the display of pages has
	been truncated."""
	if ":blog:clippedrange" not in context:
		return ''
	rv = context[":blog:clippedrange"]
	t = time.localtime(rv[0])
	l1 = pageranges.gen_monthlink(context, t.tm_year, t.tm_mon)
	pg = context.model.get_virtual_page(context.page.me(), "%d" % t.tm_year)
	l2 = htmlrends.makelink("%d" % t.tm_year, context.url(pg))
	return "%s %s" % (l1, l2)
htmlrends.register("blog::seemonthyear", month_cutoff)

# This plays around with the innards of here, and as such is not
# in conditions.py.
def isclipped(context):
	"""Succeeds (by generating a space) if we are in a blog
	view that is clipped. Fails otherwise."""
	if context.view != "blog":
		return ''
	dl = context.cache_page_children(context.page)
	if not dl:
		return ''
	# We clone the context so that any clip variables that
	# clipDown() will set do not contaminate the current
	# context. Various renderers consider their presence
	# a sign, and we may not want that.
	d2 = clipDown(context.clone(), dl)
	if len(d2) != len(dl):
		return ' '
	else:
		return ''
htmlrends.register("cond::blogclipped", isclipped)

#
# Generate a blog-style index of page titles, by (reverse) date. This
# is hard to format in separate renderers, so it actually hardcodes
# more than usual. We would like to do this in a <dl> instead of a
# table, except that Firefox does not support 'display: compact'
# and I consider that essential.
def titleindex(context):
	"""Like _blog::blog_, except that instead of rendering entries
	through a template, it just displays a table of dates and entry
	titles (or relative paths for entries without titles), linking
	to entries and to the day pages. Respects VirtualDirectory
	restrictions. Unlike _blog::blog_, it always displays information
	for all applicable entries."""
	if context.page.type != "dir":
		return ''
	if not context.page.displayable():
		raise derrors.IntErr, "undisplayable directory page"

	# This automatically applies restrictions.
	dl = context.cache_page_children(context.page)
	if not dl:
		return ''

	# Building a table is unfortunately much more complicated
	# than a <dl> would be, because we have to use <br> to separate
	# multiple entries for the same day instead of <td>, which means
	# that we have to keep track of when we need to generate one and
	# so on.
	rl = ['<table class="blogtitles">\n',]
	lday = None
	dupDict = {}
	rootpath = context.page.me().path
	# Rather than directly use a wikirend routine by importing
	# it, we indirect through the renderer registration. Since
	# either way we know a wikirend name, I figure this is no
	# worse.
	rfunc = htmlrends.get_renderer("wikitext:title:nolinks")
	for ts, path in dl:
		# FIXME: this duplication is code smell.
		np = context.model.get_page(path)
		if not np.realpage() or np.is_util() or \
		   not np.access_ok(context):
			continue
		pageid = np.identity()
		if pageid in dupDict:
			continue
		else:
			dupDict[pageid] = None

		# Do we need to generate a new row for a new day?
		# Our basic running state is that we are always in
		# a <td> for page links (except right at the start),
		# so we must close it off et cetera and then reopen
		# it.
		t = time.localtime(ts)
		plain = "%d-%02d-%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
		if plain != lday:
			if lday:
				# Not first entry ever, so close off
				# the last day table row.
				rl.append("\n</td> </tr>\n")
			rl.append("<tr> <td> %s: </td> <td>\n" % \
				  link_to_tm(context, t, plain))
			lday = plain
		else:
			# If we are the second or later entry for a
			# given day, we must put a <br> between ourselves
			# and the previous entry.
			rl.append("<br>\n")

		# As usual, we must work in a new context.
		nc = context.clone_to_page(np)
		ltitle = rfunc(nc)
		if not ltitle:
			ltitle = httputil.quotehtml(path[len(rootpath)+1:])
		# We can't use htmlrends.makelink() because that would
		# quote the live HTML in real titles.
		rl.append('    <a href="%s">%s</a>' % \
			  (context.nurl(np), ltitle))
		context.newtime(nc.modtime)
	# Done all; close off the <table>
	rl.append('\n</td></tr></table>\n')
	return ''.join(rl)
htmlrends.register("blog::titles", titleindex)

# View registration.
views.register('blog', views.TemplateView, onDir = True, onFile = False,
	       pubDir = True)
views.register('blogdir', views.TemplateView, onDir = True, onFile = False,
	       pubDir = True)
