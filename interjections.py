#
# The renderers here inject wikitext pages (so far) that are not the
# current page. Because renderers take no arguments, this means that
# they currently have to hardcode what they're injecting.
#
# (It's possible that the general answer is a 'inject this page as
# wikitext' renderer, especially if it steals template search paths.)

import utils
import htmlrends, wikirend
import views

def render_page(ctx, page):
	nc = ctx.clone_to_page(page)
	res = wikirend.render(nc)
	ctx.newtime(nc.modtime)
	return res

def inject(context, pname):
	page = context.page.me().curdir()
	if page.type != "dir":
		return ''
	np = page.child(pname)
	# We don't have to check access when we render, because rendering
	# does it implicitly.
	if np.type != "file" or not np.displayable():
		return ''
	return render_page(context, np)

def readme(context):
	"""Insert the wikitext file ((__readme)), in HTML form, if such
	a file exists in the current directory."""
	return inject(context, "__readme")
htmlrends.register("inject::readme", readme)

def index(context):
	"""Insert the wikitext file ((__index)) in HTML form, if such
	a file exists in the current directory."""
	return inject(context, "__index")
htmlrends.register("inject::index", index)

#
# Not just in the current directory.
def upreadme(context):
	"""Like _inject::readme_, except it searches for ((__readme)) all
	the way back to the DWiki root directory, not just in the current
	directory."""
	page = context.page.me().curdir()
	if page.type != "dir":
		return ''

	for pdir in utils.walk_to_root(page):
		rpage = pdir.child("__readme")
		if rpage.exists():
			break
	else:
		return ''

	# Once we find something called __readme we stop looking, even
	# if the found thing is not a file or not displayable.
	if rpage.type != "file" or not rpage.displayable():
		return ''
	return render_page(context, rpage)
htmlrends.register("inject::upreadme", upreadme)

def blogreadme(context):
	"""Like _inject::readme_, except it looks for ((__readme))
	only in the 'blog directory', the directory that made the
	blog view the default view. If there is no such directory
	between the current directory and the DWiki root directory,
	this does nothing."""
	(pv, vdir) = context.pref_view_and_dir(context.page.curdir())
	if pv != "blog":
		return ''
	rpage = vdir.child("__readme")
	if rpage.type != 'file' or not rpage.displayable():
		return ''
	return render_page(context, rpage)
htmlrends.register("inject::blogreadme", blogreadme)

#
# The 'index' view is kind of peculiar. It is a template view, except
# that if __index exists in the current directory and is a redirect,
# we redirect to it instead of rendering a template.
class IndexView(views.TemplateView):
	def redirect_page(self):
		if super(IndexView, self).redirect_page():
			return True

		# We should only be valid on directories, so we can just
		# go here.
		ip = self.page.child("__index")
		if ip and ip.is_redirect():
			# Code smell: duplicated with the core view.
			res = ip.redirect_target()
			if not res or res[0] == 'page' and not res[1]:
				self.error('nopage')
				return True
			if res[0] != 'page':
				url = res[1]
			else:
				url = self.context.uri(res[1])
			self.response.redirect(url)
			return True
		# We do not try to redirect to the regular view of this
		# directory if there is no __index file, because we would
		# have to go through gyrations to insure that we aren't
		# redirecting to something with a default index view.
		# If you ask for an index view of a directory without an
		# __index, you get whatever the view template says you
		# should get.

		# Nothing special.
		return False
views.register("index", IndexView, onDir = True, onFile = False, pubDir = True)
