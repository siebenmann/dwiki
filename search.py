#
# Extremely, extremely low-rent search.
# I am going to burn in some small hell for this.

import htmlrends, views
import re

#
# Okay, less low-rent than it used to be.

# Find an identifier in some data.
# Implementation: regexp search looking for word boundaries around the
# identifier (as literal text).
def find_id_in(iden, data):
	iden = re.escape(iden)
	pat = re.compile(r"(?:\b|[a-z0-9])%s(?:\b|[A-Z])" % iden)
	return bool(pat.search(data))

def find_in(what, data):
	if find_id_in(what, data):
		return True
	what = re.escape(what)
	pat = re.compile(r"(?:\b|_)%s" % what, re.IGNORECASE)
	return bool(pat.search(data))

# This is a brute-force search of all pages from the root downwards.
# Our sole concession to efficiency is that we turn off page caching
# during the search.
def search_pages(context, args, findfunc = find_id_in):
	flist = context.model.get_page("").descendants(context)

	# We brute-force search all pages.
	hitlist = []
	context.model.set_cache(False)
	for mtime, path in flist:
		if path == context.page.path:
			continue
		pg = context.model.get_page(path)
		if not pg.realpage():
			continue
		for iden in args:
			if pg.path == iden or \
			   findfunc(iden, pg.path):
				hitlist.append(path)
				break
			# We only bother with expensive access
			# control checks if it's a hit to start
			# with. The odds are against us.
			if findfunc(iden, pg.contents()) and \
			   pg.access_ok(context):
				hitlist.append(path)
				break
	context.model.set_cache(True)
	return hitlist

def can_search(context):
	if "search-on" not in context:
		return False
	if context["search-on"] == "authenticated":
		if not context.model.has_authentication() or \
		   not context.current_user():
			return False
	return True

#
# Renderers to display the results of all of this minimal effort.
search_form = '<form method=get action="%s">Search: <input name=search size=15></form>'
def searchbox(context):
	"""Create the search form, if searching is enabled."""
	if not can_search(context):
		return ''
	# We use the root instead of some synthetic paths because that
	# seems representative of what's actually going on.
	# This is a GET form, so we are not screwing ourselves on various
	# issues that way.
	return search_form % context.web.url_from_path("")
htmlrends.register("search::enter", searchbox)

def display_results(context):
	"""Display the results of a search."""
	if not can_search(context):
		return ''
	data = context.getviewvar("search")
	if not data:
		return ''
	data = data.strip()
	if not data:
		return ''
	hlist = search_pages(context, [data], find_in)
	if not hlist:
		return ''
	hlist.sort()
	res = []
	res.append("<ul>\n")
	for path in hlist:
		res.append("<li>")
		res.append(htmlrends.makelink(path,
					      context.web.url_from_path(path)))
		res.append("\n")
	res.append("</ul>\n")
	return ''.join(res)
htmlrends.register("search::display", display_results)

# View registration.

#
# Search view is always a view on the root, so it can't be redirected
# off the root.
class SearchView(views.TemplateView):
	def render(self):
		if not can_search(self.context):
			self.error("badaccess")
		else:
			super(SearchView, self).render()
	def redirect_root(self):
		return False

# In theory you can use ?search=... on anything, although we only
# generate it on the root.
# We do not POST to search, so this is actually disallowed.
views.register("search", SearchView, getParams = ("search",), onDir = True)
