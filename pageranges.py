#
# Take a list of pages and run them through some restriction.
# (Actually it's a list of (modtime, path) tuples, but small difference.)

import re
import time, calendar, datetime

import derrors, utils
import htmlrends
import rendcache

virt_path_var = ":virtual:suffix"

# Take a path suffix and a context and return True if the path suffix
# is a valid restriction. In the process, set the context up for us
# to filter with the restriction later.
rest_type = ":restriction:type"
rest_val = ":restriction:val"
rest_hitstore = ':restriction:navdata'

restMatches = {
	'latest': re.compile('latest/(\d+)?$'),
	'oldest': re.compile('oldest/(\d+)?$'),
	'calendar': re.compile('(\d{4})(?:/(\d\d?)(?:/(\d\d?))?)?$'),
	'range': re.compile('range/(\d+)-(\d+)$'),
	# VirtStems is used to catch the stems of things that normally
	# require arguments. When this happens they get some sort of
	# default arguments.
	'VirtStems': re.compile('(latest|range|oldest)$'),
	}
defVals = {
	'latest': 1,
	'oldest': 1,
	'range': (1, 1),
	}

def restrict(context, suffix):
	for k, rex in restMatches.items():
		mo = rex.match(suffix)
		if mo:
			break
	if not mo:
		return False

	# We found something. The only tricky bit is setting up the
	# parameters.
	context.setvar(rest_type, k)

	if k == 'VirtStems':
		context.setvar(rest_type, mo.group(1))
		context.setvar(rest_val, defVals[mo.group(1)])
	elif k == 'latest' or k == 'oldest':
		context.setvar(rest_val, int(mo.group(1)))
	elif k == 'calendar':
		cv = []
		for g in mo.groups():
			if g:
				cv.append(int(g))
			else:
				cv.append(None)
		context.setvar(rest_val, tuple(cv))
	elif k == 'range':
		# We're easy on this one; you can specify anything you
		# like.
		one = int(mo.group(1))
		two = int(mo.group(2))
		if one > two:
			two, one = one, two
		if one == 0:
			# This guarantees both one and two are positive,
			# since two is >= one.
			one += 1
			two += 1
		context.setvar(rest_val, (one, two))
	else:
		raise derrors.IntErr, "unhandled restriction match: "+k
	return True

# Attempt to virtualize the current page. If the page is virtualizable,
# we set the context up for that virtualization and return the new page;
# otherwise we return None.
def virtualize_page(context):
	if context.page.exists():
		return False
	page = context.page
	suf = []
	while not page.exists() and page.path != '':
		suf.append(page.name)
		page = page.parent()

	# To start with, we have to be virtualizing a directory.
	# We can't virtualize files; don't even go there. (No, we
	# don't have a '/comments' virtual page for the comments
	# on a given page. That's a view.)
	if not page.type == "dir":
		return False

	# We refuse to virtualize the root if displaying the root would
	# normally redirect you to another page.
	# This is debateable.
	if page.path == '' and \
	   context.model.get_page(context.wiki_root()).displayable():
		return False

	suf.reverse()
	suffix = '/'.join(suf)
	if restrict(context, suffix):
		context.setvar(virt_path_var, suffix)		
		return context.model.get_virtual_page(page, suffix)
	else:
		return False

def is_virtualized(context):
	return virt_path_var in context

def is_restriction(context):
	return rest_type in context
def restriction(context):
	if not is_restriction(context):
		return None
	rtype = context[rest_type]
	if rtype == 'calendar':
		# Calendar turns into year/month/day, depending.
		rval = context[rest_val]
		if rval[2]:
			return 'day'
		elif rval[1]:
			return 'month'
		else:
			return 'year'
	else:
		return rtype

# Convert a calendar range to two Date objects, one the start and
# one the end day of the range.
# crange[0] = year, crange[1] = month, crange[2] = day.
# None/zero is 'not set', ie the range covers the entire month or the entire
# year. Year is always set.
def crange_to_limits(crange):
	if crange[2]:
		t = datetime.date(crange[0], crange[1], crange[2])
		return (t, t)
	elif crange[1]:
		_, dcnt = calendar.monthrange(crange[0], crange[1])
		return (datetime.date(crange[0], crange[1], 1),
			datetime.date(crange[0], crange[1], dcnt))
	else:
		return (datetime.date(crange[0], 1, 1),
			datetime.date(crange[0], 12, 31))

# basically cmp(modtime, (start, end)):
# returns: 0 if modtime falls within start to end
#	   1 if modtime > end
#	   -1 if modtime < start
def calendar_cmp(start, end, modtime):
	t = datetime.date.fromtimestamp(modtime)
	if t < start:
		return -1
	elif t > end:
		return 1
	else:
		return 0

# Filter a (modtime, path) list based on the restriction chosen through
# virtualization.
def filter_files(context, flist):
	if not is_restriction(context):
		return flist
	rtype = context[rest_type]
	rargs = context[rest_val]

	# We need flist as a real list and sorted with the most recent
	# first. We start by stashing away some data for later use.
	flist = list(flist)
	utils.sort_timelist(flist)
	context.setvar(rest_hitstore, len(flist))

	if rtype == 'latest':
		return flist[:rargs]
	elif rtype == 'oldest':
		return flist[-rargs:]
	elif rtype == 'range':
		# Python makes this all work out for us. start and
		# end are one-based.
		start, end = rargs
		return flist[start-1:end]
	elif rtype == "calendar":
		rl = []
		just_before = None
		just_later = None
		# FIXME
		# The day, month, or year may be invalid, in which case
		# the datetime.date conversion in crange_to_limits()
		# will throw a ValueError. Catching it here is a crude
		# hack.
		try:
			r1, r2 = crange_to_limits(rargs)
		except ValueError:
			context.setvar(rest_hitstore, (None, None))
			return rl
		for e in flist:
			r = calendar_cmp(r1, r2, e[0])
			if r > 0:
				just_before = e[0]
			elif r < 0:
				just_later = e[0]
				break
			else:
				rl.append(e)
		context.setvar(rest_hitstore, (just_before, just_later))
		flist = rl
	# ... for now
	return flist

#
# -----
# Navigation breadcrumbs for page ranges.
# Calendar navigation is quite rough, but I think the answer to that is
# a calendar display or a calendar view.
#
# The complication in navigation stripes for restricted ranges is
# figuring out how to swan around in the range.
# Unlike most other things, these explicitly preserve the view
# in the link if necessary.
#
# The format for this stuff is debateable. This looks like the
# LiveJournal format, which is good enough for me right now.
prev_msg = "Previous %d"
next_msg = "Next %d"
prev_cal_msg = "Previous %s"
next_cal_msg = "Next %s"

def vp_from_suf(context, suf, msg, nf = False):
	tp = context.model.get_virtual_page(context.page.me(), suf)
	return htmlrends.makelink(msg, context.url(tp), nf)

def range_prev(context, start, end):
	if rest_hitstore in context:
		end = min(end, context[rest_hitstore])
	gap = end - start + 1
	if gap <= 0:
		return ''
	if rest_hitstore in context and end == context[rest_hitstore]:
		psuf = 'oldest/%d' % gap
	else:
		psuf = "range/%d-%d" % (start, end)
	return vp_from_suf(context, psuf, prev_msg % gap, True)

def latest_rel(context):
	curv = context[rest_val]
	return (range_prev(context, curv+1, curv*2), '')

def oldest_rel(context):
	# If we don't have the length, we're just dead in the water.
	if not rest_hitstore in context:
		return ('', '')
	curv = context[rest_val]
	tlen = context[rest_hitstore]
	end = tlen - curv
	start = end - curv + 1
	if start <= 1:
		start = 1
	next = htmlrends.makelink(next_msg % (end-start+1),
				  gen_range_url(context, start, end),
				  True)
	return (next, '')
	

def gen_range_url(context, start, end):
	if start > 1:
		fmt = "range/%d-%d" % (start, end)
	else:
		fmt = "latest/%d" % end
	tp = context.model.get_virtual_page(context.page.me(), fmt)
	return context.url(tp)

def range_rel(context):
	start, end = context[rest_val]
	gap = end - start + 1

	prev = range_prev(context, end+1, end+gap)
	# Next has to clip the range.
	if start > 1:
		end = start - 1
		start = start - gap
		if start <= 0:
			start = 1
		next = htmlrends.makelink(next_msg % (end-start+1),
					  gen_range_url(context, start, end),
					  True)
	else:
		next = ''
	return (prev, next)

# This only produces any results if we have already processed the
# restriction, because frankly I am not going to write that much
# code. kthanks.
def cal_link(context, ctup, ts, msg, msg2):
	if not ts:
		return msg2
	t = time.localtime(ts)
	if ctup[1] == None:
		suf = "%d" % t.tm_year
		what = "year"
	elif ctup[2] == None:
		suf = "%d/%02d" % (t.tm_year, t.tm_mon)
		what = "month"
	else:
		suf = "%d/%02d/%02d" % (t.tm_year, t.tm_mon, t.tm_mday)
		what = "day"
	# UGLY HACK
	if '%' in msg:
		mstr = msg % what
	else:
		mstr = msg
	return vp_from_suf(context, suf, mstr)
		
def calendar_rel(context):
	if not rest_hitstore in context:
		return False
	before, after = context[rest_hitstore]
	rv = context[rest_val]
	return (cal_link(context, rv, after, prev_cal_msg, ''),
		cal_link(context, rv, before, next_cal_msg, ''))

# We split the job of creating the entries for each different type
# of thing into different functions, so that things don't become
# even more messy than they already are.
rangeOps = {'latest': latest_rel, 'range': range_rel, 'calendar': calendar_rel,
	    'oldest': oldest_rel}
def rangebar(context):
	"""Display a simple range navigation bar inside a VirtualDirectory."""
	if not is_restriction(context):
		return ''
	r = rangeOps[context[rest_type]](context)
	if not r:
		return ''
	r = " | ".join([x for x in r if x])
	if not r:
		return ''
	return '<div class="rangenav"> (%s) </div>' % r
htmlrends.register("range::bar", rangebar)

#
# ---------------
def entriesIn(context, ctuple):
	dl = context.cache_page_children(context.page.me())
	if not dl:
		return False
	cstart, cend = crange_to_limits(ctuple)

	# We search for days a lot; this case is worth optimizing
	# specifically.
	# NOTE: this is lame.
	if cstart == cend:
		for e in dl:
			t = datetime.date.fromtimestamp(e[0])
			if cstart == t:
				return e[0]
			elif t < cstart:
				return False
		return False

	# general case.
	for e in dl:
		res = calendar_cmp(cstart, cend, e[0])
		if res == 0:
			return e[0]
		# If we've passed the time, we can stop now.
		if res == -1:
			return False
	return False

# I may change my mind about this one.
months = ( 'January', 'February', 'March', 'April', 'May', 'June', 'July',
	   'August', 'September', 'October', 'November', 'December', )
def genScopeRange(ctuple):
	if ctuple[1]:
		sday, daynum = calendar.monthrange(ctuple[0], ctuple[1])
		return [("%d" % x, (ctuple[0], ctuple[1], x)) for x in
			range(1, daynum+1)]
	elif ctuple[0]:
		return [(months[x][:3], (ctuple[0], x+1, None)) for x in
			range(0, 12)]
	else:
		raise derrors.IntErr, "genScopeRange doesn't do years"

# Generate a bar of links to things that actually have entries in
# them.
def genBar(context, scopelist):
	res = []
	for label, ctup in scopelist:
		t = entriesIn(context, ctup)
		if t:
			link = cal_link(context, ctup, t, label, None)
			res.append(link)
	return ' '.join(res)

# Given a range start and a range end, scan looking for things outside
# the range. Return the adjacent entries as a tuple, with None for ones
# that weren't found.
# If I was really clever I could do this with timestamp comparisons,
# having turned cstart and cend into them.
def outsideRange(context, cstart, cend):
	before = None
	after = None
	cstart1, _ = crange_to_limits(cstart)
	_, cend2   = crange_to_limits(cend)
	for e in context.cache_page_children(context.page.me()):
		r = calendar_cmp(cstart1, cend2, e[0])
		if r > 0:
			before = e[0]
		elif r < 0:
			after = e[0]
			return (before, after)
	return (before, after)

# Generate a calendar bar.
# If we are at a year level, the calbar shows months. If
# we are at the month or day level, the calbar shows days.
# If we are unrestricted but clipped, the calbar shows days,
# because we link down to days for the 'for more see ...' stuff.
def calbar(context):
	"""With blog::blog, generates a calendar-based navigation bar."""
	if is_restriction(context):
		rc = restriction(context)
		if rc not in ('year', 'month', 'day'):
			return ''
		ctup = context[rest_val]
	elif ':blog:clippedrange' in context:
		hv = context.cache_page_children(context.page.me())[0]
		t = time.localtime(hv[0])
		ctup = (t.tm_year, t.tm_mon, t.tm_mday)
	else:
		return ''
		
	scopelist = genScopeRange(ctup)
	bar = genBar(context, scopelist)

	ystr = "%d" % ctup[0]
	ylink = vp_from_suf(context, ystr, ystr)
	if len(scopelist) != 12:
		when = months[ctup[1]-1]
		mlink = vp_from_suf(context, "%d/%02d" % (ctup[0], ctup[1]),
				    when)
		what = "day for %s %s" % (mlink, ylink)
	else:
		when = "%d" % ctup[0]
		what = "month for %s" % ylink

	if not bar:
		return ''
	bstr = 'By %s: %s' % (what, bar)
	before, after = outsideRange(context, scopelist[0][1],
				     scopelist[-1][1])
	r = [bstr]
	r.append(cal_link(context, ctup, after,
			  "before %s" % when, ''))
	r.append(cal_link(context, ctup, before, "after %s" % when, ''))
	rstr = "; ".join([x for x in r if x])
	return '<div class="calbar"> %s. </div>' % rstr
htmlrends.register("range::calbar", calbar)

# A rangebar that respects the blog view's clipping.
def blograngebar(context):
	"""With blog::blog, generates a day navigation bar if the
	display of pages has been truncated."""
	# No clipping: easy, we defer to the rangebar.
	# (This will generate nothing if we have no restriction either.)
	if not ':blog:clippedrange' in context:
		return rangebar(context)

	# This is helpfully the point just before the clip, and we
	# know it falls on a day boundary.
	clipv = context[':blog:clippedrange']

	# We must always clip down to day boundaries. The numbers
	# are actually arbitrary, since the real numbers come from
	# the before/after values. We pick them to cause explosions
	# if they are ever looked at seriously.
	ctup = (-1, -1, -1)

	# Don't mangle the real context with our bizarreness.
	context = context.clone()

	# Inside an existing date restriction? Groovy, we've got
	# stuff already mostly set up.
	if is_restriction(context):
		before, after = context[rest_hitstore]
	else:
		before = None
		
	context.setvar(rest_hitstore, (before, clipv[0]))
	context.setvar(rest_val, ctup)
	context.setvar(rest_type, 'calendar')
	return rangebar(context)
htmlrends.register("range::blogrange", blograngebar)

def blogbackmore(context):
	"""With blog::blog, generate a 'or back N more' link if the display
	of pages has been truncated outside of a VirtualDirectory context."""
	if is_restriction(context):
		return ''
	if ":blog:clipsize" not in context:
		return ''
	cs = context[':blog:clipsize']
	return range_prev(context, cs+1, cs*2)
htmlrends.register("range::moreclip", blogbackmore)

#
# This is here because we (re)use the months array, although
# logically it is part of blogdir stuff.
def datecrumbs(context):
	"""Create date breadcrumbs for the blog directory if the
	current page is in a blog directory but is not being
	displayed inside a virtual directory. The 'blog directory'
	is the directory that made the blog view the default view."""
	if context.page.type != "file":
		return ''
	if is_virtualized(context):
		return ''
	# Utility pages make relatively little sense to generate
	# date breadcrumbs on, because we don't really consider them
	# part of the blog. (This is debateable, but I think it makes
	# more sense this way.)
	if context.page.is_util():
		return ''
	dirp = context.page.curdir()
	(pv, vdir) = context.pref_view_and_dir(dirp)
	if pv != "blog":
		return ''
	else:
		dirp = vdir

	ts = time.localtime(context.page.timestamp)
	r = []
	for i in (("%d/%02d/%02d" % (ts.tm_year, ts.tm_mon, ts.tm_mday),
		   "%02d" % ts.tm_mday),
		  ("%d/%02d" % (ts.tm_year, ts.tm_mon),
		   months[ts.tm_mon-1]),
		  ("%d" % ts.tm_year, "%d" % ts.tm_year)):
		page = context.model.get_virtual_page(dirp, i[0])
		r.append(htmlrends.makelink(i[1], context.url(page)))
	return ' '.join(r)
htmlrends.register("blog::datecrumbs", datecrumbs)

# Given a context.page (which had better be a directory), generate a
# properly-named link to a specific year and month, ala the month link
# in datecrumbs.
# This is here because it reuses the month array.
def gen_monthlink(context, year, month):
	vp = context.model.get_virtual_page(context.page.me(),
					    "%d/%02d" % (year, month))
	return htmlrends.makelink(months[month-1], context.url(vp))

# At this point, this is here mostly because datecrumbs() is too.
# (Initially it generated /range/N-N/ virtual directories and so
# played with the internals of the range handling, but this is no
# longer the case.)
def genlink(context, pg, msg):
	if pg:
		return htmlrends.makelink(msg, context.nurl(pg))
	else:
		return ''

def steppage(context, dl, i, step):
	i = i + step
	while 0 <= i < len(dl):
		pg = context.model.get_page(dl[i][1])
		if pg.realpage() and not pg.is_util():
			return i
		i = i + step
	return -1

def steppage_pos(context, ppath, dl):
	for i in range(0, len(dl)):
		if dl[i][1] == ppath:
			break
	else:
		# Something very funny is going on, so we have to bail.
		return None

	# Find and generate the adjacent pages, skipping utility
	# pages, redirections, etc. We do it this way to avoid
	# loading boatloads of pages before we find our place in
	# the list; in the normal case, we will load exactly two
	# pages, the pages we want.
	ppos = steppage(context, dl, i, 1)
	npos = steppage(context, dl, i, -1)
	# FIXME: too unstructured
	return (i, ppos, npos)

def page_from_pos(ctx, dl, pos):
	if pos == -1:
		return None
	else:
		return ctx.model.get_page(dl[pos][1])
def pages_from_pos(ctx, dl, res):
	return (page_from_pos(ctx, dl, res[1]),
		page_from_pos(ctx, dl, res[2]),	dl)

# A position is valid if the page timestamp for the page in the list
# is the same as the page's current timestamp. *if* the position is
# valid at all.
def valid_pos(ctx, dl, pos):
	return (pos == -1) or \
	       page_from_pos(ctx, dl, pos).timestamp == dl[pos][0]

def gen_pnp_direct(context, vdir):
	nc = context.clone()
	dl = nc.cache_page_children(vdir)
	r = steppage_pos(context, context.page.path, dl)
	if not r:
		return None
	else:
		return pages_from_pos(context, dl, r)

def gen_pnpages(context, vdir):
	# Disabled? Skip entirely.
	if not rendcache.cache_on(context.cfg):
		return gen_pnp_direct(context, vdir)

	# Even if we get a cache hit, we must super-validate it. We do so
	# by checking that the timestamps of ourselves and our next and
	# previous links are the same as in the list.
	res = rendcache.fetch_gen(context, vdir.path, 'pnc-kids')
	if res:
		r = steppage_pos(context, context.page.path, res)
		if r and \
		   valid_pos(context, res, r[0]) and \
		   valid_pos(context, res, r[1]) and \
		   valid_pos(context, res, r[2]):
			return pages_from_pos(context, res, r)

	# Miss; generate.
	r = gen_pnp_direct(context, vdir)
	if not r:
		return r
	dl = r[2]

	# The validator for the list is somewhat big; it is the timestamp
	# of every directory in the list, explicitly including the root
	# of the list. Unfortunately we have to manually deduce this from
	# the files in the list, which means that we can accidentally
	# omit a directory that currently has no files. This is really
	# a fault of the cache_page_children() approach, but meh.
	v = rendcache.Validator()
	ds = {}
	v.add_mtime(vdir)
	ds[vdir.path] = True
	for ts, ppath in dl:
		pdir = utils.parent_path(ppath)
		if pdir in ds:
			continue
		ds[pdir] = True
		v.add_mtime(context.model.get_page(pdir))
	rendcache.store_gen(context, 'pnc-kids', vdir.path, dl, v)
	return r

def prevnextcrumbs(context):
	"""Create Previous and Next links for the current page if the
	current page is in a blog directory but is not being displayed
	inside a virtual directory. The 'blog directory' is the directory
	that made the blog view the default view."""
	if context.page.type != "file" or \
	   is_virtualized(context) or context.page.is_util():
		return ''
	(pv, vdir) = context.pref_view_and_dir(context.page.curdir())
	if pv != "blog":
		return ''

	# We must clone the context because we are about to use a
	# caching operation to simplify our lives (and because it
	# is the easiest interface). We don't want to confuse things
	# by having a live cache in the actual real context.
	# (We could avoid this, but at the cost of duplicating the
	# work that cache_page_children() is already doing.)
	#nc = context.clone()
	#dl = nc.cache_page_children(vdir)

	# Find the page's place in the time-ordered list.
	#ppath = context.page.path
	#for i in range(0, len(dl)):
	#	if dl[i][1] == ppath:
	#		break
	#else:
	#	# Something very funny is going on, so we have to bail.
	#	return ''

	# Find and generate the adjacent pages, skipping utility
	# pages, redirections, etc. We do it this way to avoid
	# loading boatloads of pages before we find our place in
	# the list; in the normal case, we will load exactly two
	# pages, the pages we want.
	#ppage = steppage(context, dl, i, 1)
	#npage = steppage(context, dl, i, -1)

	r = gen_pnpages(context, vdir)
	if not r:
		return ''
	(ppage, npage, _) = r

	# Having found the previous and next pages, generate links
	# that go directly to them.
	tl = (genlink(context, ppage, "Previous"),
	      genlink(context, npage, "Next"))

	r = " | ".join([x for x in tl if x])
	if not r:
		return ''
	return '(%s)' % r
htmlrends.register("blog::prevnext", prevnextcrumbs)

def invirtual(context):
	"""Succeed (by generating a space) if we are in a VirtualDirectory
	(either directly or during rendering of a subpage). Fails otherwise."""
	if is_restriction(context):
		return ' '
	else:
		return ''
htmlrends.register("cond::invirtual", invirtual)
