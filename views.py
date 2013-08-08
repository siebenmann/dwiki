#
# The generic support for views in dwiki.
#
# The job of a view is to take a context and produce a response.
# Responses are taken to be htmlresp.Response objects by default,
# since those are actually pretty generic.
#
# This is pretty web-centric at the moment. Tough.

import utils, template
import htmlresp, htmlerr

# Register and keep track of the available views and their properties.
# This is used by the htmlview.py code, and everybody else, to make
# adding new views as simple as possible.
# By default, register() sets up a GET-only, file-only view.
#
# get_view() et al return this structure, which is more or less public.
# The initializer is not complete; it just covers things that are used
# by functions on the object.
class Struct(object):
	def __init__(self, **kwargs):
		for k, v in kwargs.items():
			setattr(self, k, v)

class ViewInfo(Struct):
	__pychecker__ = 'no-classattr'
	def accepts_page(self, page):
		if page.type == 'dir':
			return self.onDir
		else:
			return self.onFile

	def accepts_command(self, command):
		if command == "POST":
			return self.canPOST
		elif command in ("GET", "HEAD"):
			return self.canGET
		else:
			return False

known_views = {}
def register(name, factory, canGET = True, canPOST = False,
	     onDir = False, onFile = True, pubDir = False,
	     getParams = [], postParams = []):
	__pychecker__ = 'no-argsused'
	# This initializes the VI struct from the (active) locals, which
	# are the explicit arguments merged with the default arguments,
	# which is exactly what we want in one line. It's still a hack.
	nv = ViewInfo(**locals())
	known_views[name] = nv

def has_view(name):
	return name in known_views

# DANGER: get_view horks a dictionary exception if you give it a bad
# view. It is the caller's responsability to call has_view() first and
# do the right thing.
def get_view(name):
	return known_views[name]

def all_views():
	return known_views.items()

# This returns a list of views valid on directories that are marked
# public.
def pub_dir_views():
	return [x for x in known_views.keys() if known_views[x].pubDir]

#
# -----
# The following class hierarchy implements view handling itself.
# GenericView is the root and handles most of the logic; other
# people inherit from it and subclass appropriately.

# Does this request use a path that is the proper way to refer to a
# directory?
def has_proper_dirpath(context):
	opage = context['page-rawname']
	return not opage or opage[-1] == '/'

# This is an abstract parent class holding common operations.
class GenericView(object):
	def __init__(self, context):
		self.context = context
		self.response = htmlresp.Response()
		if "charset" in context:
			self.response.setCharset(context["charset"])
		# We'll be using this a lot.
		self.page = context.page
		self.model = self.context.model
		self.web = self.context.web
		self.view = self.context['view-format']

	def error(self, what, code = 404):
		htmlerr.error(what, self.context, self.response, code)

	# Check to see if a page is okay. Pages may be non-okay in
	# several ways:
	# 1: the page name can be mangled in a way that should not
	#    happen in a well-formed HTTP request
	# 2: the page name can contain things we don't serve (eg, a
	#    request for an RCS directory).
	# 3: the page might not exist.
	# 4: the page might exist but not be displayable.
	#
	# Lacking permissions is handled much later, by renderers
	# (since a page can be made up of many components, each with
	# different permissions).
	# As a side effect of returning False, we set the response
	# up.
	def page_ok(self):
		code = 404
		if utils.boguspath(self.page.path):
			error = "badrequest"
		elif not utils.goodpath(self.page.path):
			error = "nopage"
		elif not self.page.exists():
			error = "nopage"
		elif not self.page.displayable():
			code = 503
			if self.page.inconsistent():
				error = "inconsistpage"
			else:
				error = "badpage"
		else:
			return True
		self.error(error, code)
		return False

	# Handle all redictions that occur in normal flow.
	# redirect_root is broken out because we often want to override
	# just it.
	def redirect_root(self):
		# If we are at the root, we must redirect to the
		# starting page, assuming we have one. If we don't, I
		# suppose we'll show a directory listing.
		# Because pages and directories have different views that
		# they accept, we redirect to the normal view of the page.
		if self.page.path == '':
			root = self.model.get_page(self.context.wiki_root())
			if root.displayable():
				self.response.redirect(self.context.nuri(root))
				return True
		return False
		
	def redirect_page(self):
		if self.redirect_root():
			return True
		
		# If a directory was requested without a trailing slash on
		# the original query, we redirect to the slashed version.
		if self.page.type == "dir" and \
		   not has_proper_dirpath(self.context):
			self.response.redirect(self.context.uri(self.context.page))
			return True
		# Alternately, if the user gave a trailing slash and this is
		# not a directory, fail it.
		elif self.page.type != "dir" and \
		     has_proper_dirpath(self.context):
			# This is not quite the right error, but I think
			# it's better than badpage.
			self.error("badformat")
			return True

		# Some directories decline to be viewed in some views.
		# This causes a redirection to the default view for the
		# directory. For obvious reasons, this only works if the
		# view is set explicitly.
		if self.page.type == "dir" and \
		   'view-format-set-explicitly' in self.context and \
		   self.model.disallows_view(self.page, self.view):
			self.response.redirect(self.context.nuri(self.context.page))
			return True

		# Is this a magic redirect page?
		if self.page.is_redirect():
			res =  self.page.redirect_target()
			# Rather than redirecting to a completely
			# bogus page that may cause us to whoop and
			# hollar in alarm, we say that this page
			# doesn't exist.
			if not res or res[0] == 'page' and not res[1]:
				self.error("nopage")
				return True
			# If the redirection is not a page redirection,
			# the url is just there; otherwise, generate it
			# from the page.
			if res[0] != 'page':
				url = res[1]
			else:
				url = self.context.uri(res[1])
			self.response.redirect(url)
			return True
		# No redirects found.
		return False

	# Must be implemented in subclasses.
	def render(self):
		pass

	def respond(self):
		if not self.page_ok():
			return self.response
		elif self.redirect_page():
			return self.response

		self.render()

		# If we are supposed to have a non-202 code as a result
		# of normal rendering, set it.
		e = htmlerr.geterror(self.context)
		if e:
			self.response.code = e
		
		# Set the last-modified time if it's available 
		if self.context.modtime > 0:
			self.response.setLastModified(self.context.modtime)
		if self.context.time_reliable:
			self.response.setTimeReliable()
			
		return self.response

#
# Now specific views.

# The class that implements 'sorry charley, view not available for this
# type of thing'. We hook render() and not respond() so that error checks
# (and redirections) happen first.
class BadView(GenericView):
	def render(self):
		self.error("badformat")

# Anything that can be handled as a normal lookup template.
class TemplateView(GenericView):
	def render(self):
		to = self.model.get_view_template(self.page, self.view)
		self.context.newtime(to.timestamp())
		if self.view == 'history':
			self.context.newtime(self.page.histtimestamp())
		self.response.html(template.Template(to).render(self.context))

# Alternate type views are just like template views, except that they
# can be used on the root directory and they have a non-default content
# type. (AltType is an abstract class. Subclass it with something that
# provides a content_type setting.)
class AltType(TemplateView):
	content_type = "SETME"
	def render(self):
		super(AltType, self).render()
		self.response.headers['Content-Type'] = self.content_type

	def redirect_root(self):
		return False

# The source view simply barfs out the content without rendering it.
class SourceView(GenericView):
	def render(self):
		# FIXME: do we have any access restrictions on who can
		# read the source format?
		# This is a corner case: it does no good to refuse to
		# show people access-restricted wikitext if they can
		# just view source to see the pre-rendered contents.
		# So we must ask a wikirend oracle if there are access
		# restrictions on the text that we fail.
		if not self.page.access_ok(self.context):
			self.error("badaccess", 403)
		else:
			self.context.newtime(self.page.timestamp)
			self.response.text(self.page.contents())

#
# Handle POST forms and their special redirection needs. These must
# usually hook respond() directly, because they make use of entirely
# synthetic and invalid/illegal page names that would otherwise have
# gone down in flames by the time we got to render().
#
def hasallvars(context, vars):
	for v in vars:
		if context.getviewvar(v) is None:
			return False
	return True

# NOTE: a POST request cannot redirect to a GET of the same URL.

# Handle POST 'views'. POST views are shortcutted massively; because
# of the 'cannot point to real URL you want to redirect to ha ha ha'
# issue above, most of them actually point to invalid synthetic URLs
# (but they have to be inside the DWiki space). Such requests carry
# around with them the real page that we should redirect to. (It is
# no skin off our teeth if a user wants to forge this, since they
# could just go to it normally.)
class PostView(GenericView):
	post_vars = ("page", )
	
	def post(self):
		# Implement me in kids.
		pass
	
	def respond(self):
		if not hasallvars(self.context, self.post_vars):
			self.error("badrequest", 403)
		else:
			self.post()
		return self.response
