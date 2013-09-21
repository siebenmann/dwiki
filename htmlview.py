#
# Format wiki data for HTML output.
#
import urllib

import derrors
import views
import pageranges

# By convention we import all HTML view renderers, thereby forcing
# their registration.
__pychecker__ = "no-import"
import htmlrends, wikirend, histview, blogdir, comments, search, atomgen
import interjections, sitemaps, htmlauth, conditions
__pychecker__ = ""
import htmlerr

# Our views.
# Most are registered elsewhere, in the files that implement them, but
# these views are so basic that we register them here, in the core.
views.register('history', views.TemplateView)
views.register('normal', views.TemplateView, onDir = True, pubDir = True)
views.register('source', views.SourceView)

#
# This implementation serves straight URLs under a rooturl,
# with views indicated by '?<view>' at the end. The rooturl is a
# directory, not just a raw prefix.
#
def pub_dir_views():
	return views.pub_dir_views()

class WebServices:
	def __init__(self, cfg, model):
		self.cfg = cfg
		self.model = model
		# .url_from_path() is such a hot path that it is worth
		# pre-computing the rooturl rather than doing it every
		# time.
		ru = self.cfg.get('publicurl', self.cfg['rooturl'])
		if ru[-1] != '/':
			ru = ru + '/'
		self.rooturl = ru

	def prefDirView(self, page):
		return self.model.pref_view_and_dir(page, pub_dir_views())[0]
	def pref_view_and_dir(self, page):
		return self.model.pref_view_and_dir(page, pub_dir_views())
	def all_dir_views(self):
		return list(pub_dir_views())

	# This returns the full path from the rooturl, guaranteed
	# to start with a / (and end with a / if the target is a
	# directory).
	# We assume that the root url is pre-quoted if necessary.
	def url_from_path(self, path, view = None, viewparams = None):
		page = self.model.get_page(path)
		if page.type == "dir" and path:
			path = path + '/'
		url = urllib.quote(path)
		if view:
			if not viewparams:
				viewparams = {}
			t = ["?%s" % urllib.quote(view)]
			for k, v in viewparams.items():
				t.append("%s=%s" % (k, urllib.quote_plus(v)))
			url = url + "&".join(t)
		return self.rooturl + url

	# uri_from_path() returns something that is usable in a
	# redirection.
	# It turns out that redirections are not normally supposed
	# to include the port; if you're pointing to the same server,
	# the web browser sort of plugs it in itself. Me, I think this
	# is spectacularly braindead, but no one asked my opinion.
	def uri_from_path(self, path, context, view = None, viewparams = None):
		return "%s%s" % (context["server-url"],
				 self.url_from_path(path, view,
						    viewparams))
	def uri_from_url(self, url, context):
		#if 'server-port' in context and \
		#   context['server-port'] != '80':
		#	return 'http://%s:%s%s' % (context['server-name'],
		#				   context['server-port'],
		#				   url)
		#else:
		# Every feed reader I've gotten to work so far *doesn't*
		# want the port included in the full URI that we return.
		# I don't understand how this is supposed to work in
		# general, but it appears that losing is bound to
		# happen.
		return "%s%s" % (context['server-url'], url)

	# Given a context, return the view for that context.
	# This is out of line due to size and genericness.
	def viewFactory(self, context):
		return viewFactory(context)

	# -------
	# Does a view exist at all?
	def view_exists(self, view):
		return views.has_view(view)
	# Given a view / method combination, is it legal?
	def view_cmd_allowed(self, view, command):
		if not views.has_view(view):
			return False
		vi = views.get_view(view)
		return vi.accepts_command(command)

	# What are its parameters?
	def view_param_list(self, view, command):
		if not views.has_view(view):
			return []
		vi = views.get_view(view)
		if command == "POST":
			return vi.postParams
		else:
			return vi.getParams

	# Given a dictionary of parameters, attempt to guess the
	# view of a GET request.
	def guess_view(self, paramdict):
		rl = []
		getvlist = [x for x in views.all_views() if x[1].getParams]
		for v, vi in getvlist:
			for param in vi.getParams:
				if param in paramdict:
					rl.append(v)
					break
		if len(rl) == 1:
			return rl[0]
		else:
			return None

	# Return whether a given view of a page is bad.
	def page_view_ok(self, context, page, view):
		if view == "index" and \
		   not context.model.is_index_dir(page):
			return False
		if context.model.disallows_view(page, view):
			return False
		if context.page != page or context.view != view:
			context = context.clone_to_page(page)
			context.setview(view)
		return not view_bad(context)

# Is the view bad, which will generate a 'bad format' error?
def view_bad(context):
	view = context.view
	# Unknown view: axiomatically bad.
	if not views.has_view(view):
		return True

	# Check to see if this view doesn't like this type of pages.
	# This handles file/directory disconnects.
	vi = views.get_view(view)
	if not vi.accepts_page(context.page):
		return True

	# History view requires history to be available.
	if view == 'history' and not context.page.hashistory():
		return True
	# It's a good view.
	return False

def viewFactory(context):
	# We must handle virtualization before anything else, because
	# otherwise we will swan around dealing with a nonexistent
	# page.
	res = pageranges.virtualize_page(context)
	if res:
		# The result is a virtualized page that replaces the
		# real page (plus a number of bits and pieces changed
		# in the context).
		context.set_page(res)
		context.setvar("pagename", res.root.name)
		context.setvar("basepage", res.root.path)
	else:
		context.setvar("basepage", context.page.path)

	# Directories have a default view type that they may express
	# a desire for; we transparently honor that desire if there
	# has not been an explicit view set.
	# Not redirecting and just showing is more transparent and
	# nicer.
	if context.page.type == "dir":
		pv = context.pref_view(context.page)
		if pv and context.view != pv and \
		   not "view-format-set-explicitly" in context:
			context.setview(pv)
	elif context.page.type == "file" and \
	     'view-format-set-explicitly' not in context and \
	     'remap-normal-to-showcomments' in context:
		context.setview('showcomments')

	# Reject the bad and the ugly.
	if view_bad(context):
		return views.BadView(context)

	# Create the actual view from the factory given.
	vi = views.get_view(context.view)
	return vi.factory(context)
