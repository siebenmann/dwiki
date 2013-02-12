#
# Expand templates given certain pieces of black magic.
#
# Expansions are [$@#]{text}, for variable, renderer, and inclusion
# expansions respectively.
# Plus %{text} for conditional renderer expansion.
#
# Errors in template expansion are fatal, because they affect the
# appearance of everything.
#
# ${} and #{} expansions have acquired a bunch of internal magic
# option characters, complicating our nice simple operating model.
# It turns out that nice and useful templating requires just slightly
# more power than it looks like on first blush.
#
import re

import derrors, htmlrends, httputil

# Core expansions.
exp_pat = re.compile(r'([$@#%]\{([^}]+)})')

# Variable expansions inside #{[<!]text}.
exp_varpat = re.compile(r'\$\(([^)]+)\)')

class ReturnNothing(Exception):
	pass

class Template:
	def __init__(self, fobj):
		self.tmpl = fobj.contents()

	def render(self, ctx):
		self.context = ctx
		try:
			return exp_pat.sub(self.subst, self.tmpl)
		except ReturnNothing:
			return ''

	def subst(self, mo):
		what = mo.group(1)[0]
		if what == '$':
			return self.variable(mo)
		elif what == '@':
			return self.renderer(mo)
		elif what == '%':
			return self.cond_renderer(mo)
		elif what == '#':
			return self.include(mo)
		else:
			raise derrors.IntErr, \
			      "unknown template operation: "+mo.group(1)

	def variable(self, mo):
		canmiss = False
		missabort = False
		key = mo.group(2)
		# Okay, straightforward must-be-present stuff is
		# somewhat lacking in features.
		if key[0] == '?':
			canmiss = True
			key = key[1:]
		elif key[0] == '!':
			missabort = True
			key = key[1:]
		if key and key[0] == '|':
			key = key[1:]
			ovars = key.split('|')
			if not ovars:
				raise derrors.RendErr, "invalid key '%s'" % mo.group(1)
			for ov in ovars:
				if ov in self.context:
					vv = self.context[ov]
					return httputil.quotehtml(vv)
		elif not key:
			raise derrors.RendErr, "invalid key '%s'" % mo.group(1)
		else:
			if key in self.context:
				return httputil.quotehtml(self.context[key])
		# Error if we have to have a value (normal case).
		if canmiss:
			return ''
		elif missabort:
			raise ReturnNothing, "variable expansion empty"
		raise derrors.RendErr, "key with no value: '%s'" % mo.group(1)

	# The context's get_render function handles all error checking
	# and explosions.
	# We avoid mysterious errors about totally empty renderer strings
	# by checking first and generating a better one.
	def renderer(self, mo):
		actor = mo.group(2)
		if not actor.strip():
			raise derrors.RendErr, "badly formed renderer macro: "+mo.group(1)
		rfunc = htmlrends.get_renderer(actor)
		return rfunc(self.context)

	# A conditional renderer causes the template to return nothing
	# if it fails to render any content.
	def cond_renderer(self, mo):
		res = self.renderer(mo)
		if not res:
			raise ReturnNothing, "template generates nothing"
		return res

	# (Attempt to) process a single template.
	def template(self, template):
		to = self.context.model.get_template(template)
		if not to:
			raise derrors.RendErr, \
			      "unknown template '%s'" % template
		res = Template(to).render(self.context)
		# The timestamps of templates are only considered
		# relevant if they expand to something. This is iffy,
		# but we can't win either way and this way is friendlier.
		# (The other way kicks *everything* any time a rarely
		# rendered template is updated; I would rather make
		# Last-Modified timestamps more useful.)
		if res:
			self.context.newtime(to.timestamp())
		# The final trailing newline in a file is an
		# implementation artifact. Because it makes things nicer
		# and closer to what the template 'should' look like if
		# the file's real text was inserted, we remove it.
		if res and res[-1] == '\n':
			return res[:-1]
		else:
			return res

	# Include expansion is complicated by all the bonus features.
	def include(self, mo):
		def _tsplit(t):
			tpl = t.split('|')
			if not tpl:
				raise derrors.RendErr, \
				      "badly formed template '%s'" % mo.group(1)
			return tpl
		template = mo.group(2)
		if template[0] == '|':
			# Multi-include that picks the first one to generate
			# content.
			for t in _tsplit(template[1:]):
				res = self.template(t)
				if res:
					return res
			return ''
		elif template[0] == "?":
			# If first generates content, expand all.
			tpl = _tsplit(template[1:])
			res = self.template(tpl[0])
			if res:
				rl = [res]
				for t in tpl[1:]:
					rl.append(self.template(t))
				return ''.join(rl)
			else:
				return ''
		elif template[0] in ('!', '<'):
			# '<' behaves like '|' and '?': if we don't find
			# anything, we return empty. '!' errors on it.
			# .expand_tnames() throws away nonexistent
			# templates for us, so this is simple.
			r = self.expand_tnames(template[1:])
			if r:
				return self.template(r[0])
			if template[0] == '!':
				raise derrors.RendErr, "Unfound template in: "+mo.group(0)
			else:
				return ''
		else:
			# Oh look, it's a *simple* case!
			return self.template(template)

	#
	# Expand first-found name components, ultimately assembling ourselves
	# into an expander for for the whole strings.

	# Regexp subst target function.
	def _exp_var(self, mo):
		varname = mo.group(1)
		if varname not in self.context:
			raise derrors.RendErr, "Bad variable name in template name: '%s'" % varname
		return self.context[varname]

	# The whole purpose of this complex routine is to expand '...'
	# operators.
	# pre is an array of the pathname prefixes we have generated so
	# far. It starts out as ['']; because of how we add paths to it,
	# this means that the final paths will all start with a '/' that
	# will have to be stripped off at the end.
	# We walk along the list of path elements. If the path element
	# is a plain one, we add it to the end of all of the prefixes
	# that exist. If it is a '...' expansion, we generate a list
	# of all of the possible expansions and add each one in turn
	# on to the end of all existing prefixes.
	# When we run out of path elements, we're done.
	def _exp_piece(self, pre, path_elems):
		while path_elems:
			piece = path_elems.pop(0)
			if piece.startswith('...'):
				vpaths = piece[3:].split('/')
				if vpaths[0] == '':
					vpaths = vpaths[1:]
				# given '...a/b/c', vpaths winds up being
				# ['a', 'b', 'c']. We create in t a list
				# of ['a/b/c', 'a/b', 'a', ''].
				t = ['/'.join(vpaths[:i]) \
				     for i in range(len(vpaths), -1, -1)]

				# Because t[-1] is '' we use a funny
				# condition so that we don't generate
				# 'Something//foo' when the dust settles.
				pre = ["%s/%s" % (x, y) if y else x \
				       for x in pre for y in t]

				# We could do all of this in a single [...]
				# but the speed gains turn out to be tiny
				# while the code gets even less clear. So,
				# no.
			else:
				pre = ["%s/%s" % (x, piece) for x in pre]

		# Strip leading / from all entries
		pre = [x[1:] for x in pre]
		return pre

	# Expand a single template name string, which may contain both
	# variable expansions and '...' operators. Note that we tokenize
	# into pathname components, then expand variables, then expand
	# ... operators. Somewhat peculiar things probably happen if you
	# expand a variable to something which includes a /.
	#
	# This order of expansion is necessary so that
	# 'foo/...$(page)/fred.tmpl' works right; we need
	# '...a/page/path' to be seen as a single token.
	#
	# If there is no ... in the name string, we cheat and skip a lot
	# of stuff. The result is indistinguishable.
	def expand_tname(self, namestr):
		ns = namestr.split('/')
		# We cannot use utils.goodpath() because '...' is not a good
		# path.
		if not namestr or '' in ns:
			raise derrors.RendErr, "badly formed template name '%s'" % namestr

		# If there is no '...' operator, all we have to do is expand
		# variables (once; there is no nested variable expansion).
		if '...' not in namestr:
			return [exp_varpat.sub(self._exp_var, namestr)]

		# There's a '...'. We must expand variables in components,
		# then actually do expansion.
		ns2 = [exp_varpat.sub(self._exp_var, x) for x in ns]
		return self._exp_piece([''], ns2)

	# Split template alternatives string on '|', then run each
	# through .expand_tname(), then figure out which ones actually
	# exist, and just return that.
	def expand_tnames(self, tstr):
		ts = [z for z in tstr.split("|") if z]
		r = []
		for te in ts:
			r.extend(self.expand_tname(te))
		r = [x for x in r if self.context.model.template_exists(x)]
		return r
