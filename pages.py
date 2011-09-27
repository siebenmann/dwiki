#
# dwiki pages.
# This is mostly model-based information.
# At least it thoroughly eliminates the pernicious path vs name issue
# the dwiki code used to have.
#
import re

import utils, access
import pageranges
	
# What is the redirection target of a given page?
# Right now this is entirely driven by the file
# contents.
# Magic redirects are a file starting with a line that says
#	REDIRECT pagename
# (with the REDIRECT right at the start)
# The file must have only a few lines; additional line contents
# are irrelevant.
#
# Also, if we have a symbolic link and it's a relative symbolic link,
# we try to interpret it as a redirect if possible (ie, if it points
# to a real page). Forcing relative links only avoids the situation
# where DWiki will show one thing and 'less file' will show another.
redir_re = re.compile(r"REDIRECT (.*)\n(?:.*\n){,9}$")
def redirect_target(page):
	t = page.exists() and page.linkval()
	if t and t[0] != '/':
		# We have to do this check here, because we want symlinks
		# that are not valid redirects to actually read their real
		# contents. (This is arguable.)
		npage = page.model.get_page_relname(page.parent(), t)
		if npage and npage.exists():
			# We must force this to be interpreted as an
			# absolute path.
			return ('page', '/' + npage.path)

	# Not a symbolic link; try regular file contents.
	if not page or page.type != 'file' or not page.displayable():
		return None
	co = page.contents()

	# We use a regular expression as the fastest way to do our
	# checks (and derive the redirection itself).
	mo = redir_re.match(co)
	if not mo:
		return None
	redir = mo.group(1)

	# This is either a http:// link, an absolute local link, or
	# a relative link. Return appropriate information.
	if redir.startswith("http://"):
		return ('url', redir)
	elif len(redir) > 2 and redir[0] == '<' and redir[1] == '>':
		return ('url', redir[1:-1])
	else:
		# Don't screw well-intentioned people who put a '/'
		# on the end of directory names in their redirects.
		if len(redir) > 1 and redir[-1] == '/':
			redir = redir[:-1]
		return ('page', redir)

# I cannot believe I am doing this category of efficiency hacks:
def memo1(func):
	def _wrap(self):
		r = func(self)
		setattr(self, func.func_name, lambda : r)
		return r
	return _wrap

class Page(object):
	def __init__(self, path, model):
		self.name, self.path = utils.canon_path(path)
		self.model = model
		self.pfile = self.model.get_pfile(path)
		self.type = self.pfile.type
		self.timestamp = self.pfile.timestamp()
		self.modstamp = self.pfile.modstamp()

	# Internal use only:
	def _get(self, page):
		return self.model.get_page(page)

	# This is used in virtualization.
	def me(self):
		return self

	# The parent of a directory is ..; the parent of a file
	# is its directory. The root is its own parent.
	def parent(self):
		if self.path == '':
			return self
		else:
			return self._get(utils.parent_path(self.path))
	parent = memo1(parent)

	# Current directory of a directory is self; of a file, the
	# parent.
	def curdir(self):
		if self.type == "dir":
			return self
		else:
			return self.parent()
	curdir = memo1(curdir)

	# Children is only defined for directories.
	def children(self, whattype = None):
		if self.type != "dir":
			return []
		clist = [self._get(utils.pjoin(self.path, z)) for
			 z in self.pfile.contents()]
		if whattype:
			clist = [z for z in clist if z.type == whattype]
		return clist
	def child(self, name):
		return self._get(utils.pjoin(self.path, name))

	def descendants(self, context):
		__pychecker__ = "no-argsused"
		if self.type != "dir":
			return []
		return context.model._pchildren(self)

	# Are we a real page?
	def realpage(self):
		return self.pfile.exists() and self.pfile.displayable() and \
		       not self.is_redirect()
	realpage = memo1(realpage)

	# This is a quick routine that doesn't have to try to generate
	# the page.
	def is_redirect(self):
		return redirect_target(self) is not None
	is_redirect = memo1(is_redirect)

	# What is the redirection target of a page?
	# Redirects are assumed to be relative and are run through
	# our canonical canonicalization process.
	def redirect_target(self):
		res = redirect_target(self)
		if not res:
			return None
		if res[0] != 'page':
			return res
		# the redirection is relative to the directory the
		# page is in, not the current page's directory.
		return ('page',
			self.model.get_page_relname(self.curdir(), res[1]))
	redirect_target = memo1(redirect_target)

	#
	# We mirror a lot from the page file. In fact, we mirror
	# everything we don't already have.
	def __getattr__(self, attr):
		return getattr(self.pfile, attr)

	#
	# -- access answers.
	def access_ok(self, context):
		return self.realpage() and access.access_ok(self, context)
	def access_on(self, context):
		return self.realpage() and access.is_restricted(self, context)
	def comment_ok(self, context):
		return self.realpage() and \
		       access.comment_ok(self, context)
	# comment_ok() is true if we can comment on the page.
	# comments_on() is true if it looks like *someone* can comment
	# on the page, and thus that the page can potentially have
	# comments.
	def comments_on(self, context):
		return self.realpage() and \
		       access.comments_on(self, context)
	
	# This returns whether or not it is OK to render a page.
	# A page is renderable if either a) the page has its own access
	# controls or b) its parents impose no access controls that would
	# block access.
	def render_ok(self, context):
		return self.realpage() and \
		       access.access_ok(self.parent(), context)

	def has_source(self):
		return self.type == "file"

	def virtual(self):
		return self != self.me()

	def is_util(self):
		return self.type == 'file' and \
		       self.model.is_util_name(self.name)
	is_util = memo1(is_util)

class VirtDir(Page):
	def __init__(self, path, model, root):
		super(VirtDir, self).__init__(path, model)
		self.root = root
		self.type = "dir"
		self.timestamp = root.timestamp
		self.modstamp = root.modstamp
		self.pfile = self.root.pfile
		self.realpath = root.path
		# We have to leave self.name alone, unfortunately.

	# __getattr__ hoists me on my own petard for some reason.
	def realpage(self):
		return self.root.realpage()
	def children(self, whattype = None):
		return self.root.children(whattype)
	def child(self, name):
		return self.root.child(name)
	def exists(self):
		return True
	def displayable(self):
		return True

	def access_ok(self, context):
		return self.root.access_ok(context)
	def render_ok(self, context):
		return self.root.render_ok(context)

	def me(self):
		return self.root

	def descendants(self, context):
		return pageranges.filter_files(context,
					       self.root.descendants(context))
