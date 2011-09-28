#
# Rend(er) wiki-text into HTML.
#
# Documentation of what exactly wiki-text consists of is not here; look
# for it in dwiki/Formatting in the DWiki test.
#
# WikiText rendering should result in valid and properly nested HTML.
# Badly nested explicit list nesting is the only exception, because
# doing it 'properly' results in ugly looking results in common browsers.
#
import re, string

import htmlrends, derrors
import macros
import rendcache

# Options for how we render things; as flags, they're OR'd together.
# Rendering options.
NOLINKS = (1<<0)
ABSLINKS = (1<<1)
NOFOLLOW = (1<<2)
TITLEONLY = (1<<3)
# internal use only
set_features_option = (1<<8)
set_title_option = (1<<9)
rendering_flags = (set_features_option | set_title_option)

# Macro restrictions.
# Normally we allow everything (except CutShort, which must be
# explicitly enabled always).
# In NOMACROS, no macros act and they render as themselves.
# In SOMEMACROS, only allowed macros render, and disallowed macros
# render as '{macro suppressed}'.
NOMACROS = (1<<16)
SOMEMACROS = (1<<17)
ALLOW_RESTRICTED = (1<<18)
ALLOW_CANCOMMENT = (1<<19)
ALLOW_CUTSHORT = (1<<20)
ALLOW_IMG = (1<<21)
macro_perms = {
	'Restricted': ALLOW_RESTRICTED,
	'CanComment': ALLOW_CANCOMMENT,
	'CutShort': ALLOW_CUTSHORT,
	'IMG': ALLOW_IMG,
	}
# Things that are blocked unless they are explicitly permitted.
macro_block_perms = {'CutShort': ALLOW_CUTSHORT,}

# Flags for various modes.
# (I think that increasingly, set_features_option is the default.)
terse_flags = (SOMEMACROS | ALLOW_RESTRICTED | ALLOW_CANCOMMENT |
	       ALLOW_CUTSHORT | ALLOW_IMG | set_features_option )

# Flags used when we are rendering to check results.
check_flags = (SOMEMACROS | ALLOW_RESTRICTED | ALLOW_CANCOMMENT |
	       set_features_option | NOLINKS)

# This collection of regular expressions matches the various classes
# of lines that can occur in wikitext. Generally speaking, the second
# match group is the content (except for cases where there IS no
# content); sometimes we have to introduce bogus groups to make this
# happen.
# All regexps are re.match()'d, not re.search()'d, so they are
# implicitly anchored at the start as well as the explicit end
# anchor.

lineClassRegexps = (
	(re.compile(r'([^\s=*>|#\d+.-].*)'),'p'), # common case - other p's below
	(re.compile(r'\s*$'),'blank'),
	(re.compile(r'(\s(\s*))(\S.*)'),'indented'), # either pre or continuation
	(re.compile(r'(=+)\s(.+)?'),'header'),
	(re.compile(r'\*\s+\*\s+\*\s*$'),'starsep'),
	(re.compile(r'(>(?:\s>)*(?:\s|$))()(.*)'),'quote'),
	(re.compile(r'\|\s+(.+)'),'table'),
	(re.compile(r'\|_.\s+(.+)'),'horitable'),
	(re.compile(r'(\*)(\s+)(.+)'),'ul'),
	# this should generate a different token, but implementation issues
	# make it simpler to fix up in the general list handler.
	(re.compile(r'(\+)(\s+)(.+)'),'ul'),
	(re.compile(r'([\d#])(\s+)(.+)'),'ol'),
	# this blows up on '- [[abc http://somesite/]]: foobar ...', so
	# we need the more complicated version.
	#(re.compile(r'(-)(\s)(\S[^:]*)(:\s+)(.+)'),'dl'),
	(re.compile(r'(-)(\s)(\S(?:[^:]|:(?!\s))*)(:\s+)(.+)'),'dl'),
	(re.compile(r'-{4,}\s*$'),'hr'),
	# The normal innerlist cannot include '-', because otherwise
	# it swallows '-- signoff', which is *not* an indented list.
	# We must match '--' only when it has a valid 'dl' format,
	# which we clone from the 'dl' regexp above.
	(re.compile(r'([0#*+])()((?:\1)+\s.*)'),'innerlist'),
	(re.compile(r'(-)()(-+\s\S[^:]*:\s+.+)'), 'innerlist'),
	# this also affects the basic case at the start, which must
	# include . as a stop character.
	(re.compile(r'\.pn\s+(.+)'), 'procnote'),
	# THIS MUST BE THE LAST CASE
	# cks keeps forgetting this.
	(re.compile(r'(\S.*)'),'p'),
	)

def expandedLength(tabsize, astr, prevPos):
	offset = prevPos
	for c in astr:
		if c != '\t':
			offset += 1
		else:
			offset -= offset % tabsize
			offset += tabsize
	return offset

def getTokFromLine(line, qoffset, linenum):
	__pychecker__ = "no-returnvalues"
	match = None
	lineClass = None
	for x in lineClassRegexps:
		match = x[0].match(line)
		if match:
			lineClass = x[1]
			break
	if lineClass == 'quote' or lineClass == 'indented' or \
		   lineClass == 'innerlist':
		newoffset = expandedLength(8, match.group(1), qoffset)
		innerTok = getTokFromLine(match.group(3), newoffset, linenum)
		return (lineClass, line, qoffset, match, innerTok)
	if lineClass == 'header' and 1 == linenum:
		lineClass = 'header1'
	return (lineClass, line, qoffset, match)

s_ents = ('i', 'b', 'tt', 'p', 'pre', 'table', 'ul', 'ol', 'li', 'blockquote',
	  'em', 'strong', 'code', 'dl', 'dt', 'dd', 'h1', 'h2',
	  'h3', 'h4', 'h5', 'h6', 'tr', 'td',
	  'big', 'small', 'strike', 'sub', 'sup', 'u', )
x = None
start_entity = dict([(x, "<%s>" % x) for x in s_ents])
end_entity = dict([(x, "</%s>" % x) for x in s_ents])
# shut pychecker up.
del x

# TODO: should have a better way of styling these. CSS, anyone?
# See http://www.w3.org/TR/REC-html40/struct/tables.html
# Also http://www.w3.org/TR/1998/REC-CSS2-19980512/tables.html
# We style with CSS these days, but supply explicit table styling to
# cope with CSS-less browsers so the result does not look like ass.
start_entity['table'] = '<table border="1" cellpadding="4">'
start_entity['td'] = '<td valign="top">'
start_entity['ntd'] = '<td valign="top" align="right">'
start_entity['horitable'] = '<table class="horizontal">'
# Easiest way to handle this:
end_entity['horitable'] = end_entity['table']
end_entity['ntd'] = end_entity['td']

#fake entity for straight-to-two lists:
start_entity['innerlist'] = '<div style="margin-left: 2em">'
end_entity['innerlist'] = '</div>'

# Bits of cell and table line matching.
table_cell_boundary_re = re.compile(r'(?<!\S)\|(?!\S)')
# This matches 'numeric' table cell entries, ones that should be set
# flush right instead of flush left. This deliberately excludes
# apparent percentiles, for reasons that I am fuzzy about.
numeric_td_match_re = re.compile(r'^(?:~~|\*)?[-+]?(?:[0-9,]+|[0-9]+\.[0-9]+)(?:~~|\*)?$')

# These surround generated wiki HTML.
wikitext_start = '<div class="wikitext">'
wikitext_end = '</div>'

# Used in splitting apart space-separated brlinks
# Because these may contain embedded newlines, we must use some flags.
lastspace_re = re.compile(r'^(.+)\s+(\S+)$', re.MULTILINE|re.DOTALL)

# Special preformatted marking. This has to occur at the very start
# of the content. (Note that it is matched against the *raw* content,
# since it includes a \n.)
# We have since expanded this to include a 'search' directive, which
# expands the search path for places to find pages.
pragma_re = re.compile(r'^#pragma (pre|plaintext|search .*)\n')
def get_pragma(data):
	mo = pragma_re.match(data)
	if not mo:
		return None
	else:
		return mo.group(1)
def is_plaintext(data):
	return get_pragma(data) in ('pre', 'plaintext')
def search_dirs(data):
	pr = get_pragma(data)
	if pr and pr.startswith("search "):
		sl = []
		# Cope with people's desires to start paths with '/'
		# to mean 'absolute, honest'. Possibly I should deal
		# with this upstream.
		for e in pr.split()[1:]:
			if e and e[0] == '/':
				e = e[1:]
			sl.append(e)
		return sl
	else:
		return []

# Valid HTML and XML cannot include certain ranges of control
# characters, so we must suppress any that pop up for correctness.
# This is especially important for XML, since a) Atom is an XML
# dialect and b) various Atom/XML parsers just refuse your document
# entirely if it's invalid.
# (See the comments in atomgen.py for more depression on this, as
# one actually needs to exclude certain *Unicode* characters too.)
#
# Control character eating in regular text is done in the prelinestuff
# matching array and inline_control(), because this approach seems the
# fastest and best. However, ((..)), [[...|]], and <pre> blocks go
# directly to inline_plaintext(), so we must also handle it there.
#
# (if we wanted to just eat control characters, or do a straight
# substitution to ? or something, string.translate might be faster.
# However, note that we expect this to almost never hit.)
#
# To do this 100% correctly we would have to worry about links and
# a few other sources of stray characters.
#
# This is not entirely correct here. The XML standard says that
# 127-159 are bad; however, we are interpreting bytes, not Unicode
# characters. UTF-8, the most likely encoding, actually uses *bytes*
# in the 128-159 for the right Unicode characters. Fixing this is
# intractable in DWiki's current model.

# This runs range(0, 32) because range() excludes the end point; the
# real range is 0-31 inclusive (minus tab, LF, and CR).
def cc_chars():
	return "".join([chr(x) for x in range(0, 32) + [127,]
			if x not in (9, 10, 13)])
def cc_pat():
	return "["+cc_chars()+"]"
cchar_re = re.compile(cc_pat())

# The processing of "inline" markup is done in a lookup table.  Before
# we can make the lookup table, however, we need to bind all the
# names, so here they are.  Note that inline_plaintext is aliased as
# htmltext inside the class.

def inline_plaintext(rend, txt):
	txt = txt.replace('&', '&amp;')
	txt = txt.replace('<', '&lt;')
	# XXX: replacing > is unnecessary and bulks up the text.
	#txt = txt.replace('>','&gt;')
	# XXX: we leave " and ' strictly alone; what the browser sees
	# is what the user wrote, not what we think should be seen.
	#txt = txt.replace('"','&quot;')
	#txt = txt.replace("'",'&#39;')

	# We must eat bad control characters. The simplest and
	# reasonably fast way is to do it with a regexp substitute,
	# which is very fast for a) short text and b) misses, which
	# is what we expect.
	txt = cchar_re.sub("{bad character}", txt)

	rend.result.append(txt)

# NOTE: inline_font is special magic, because it gets and returns the
# text. (Under some circumstances it does lookahead and the like,
# and swallows extra text.)
ere_text = '(.*?)[^\s]X'; sre_text = 'X[^\s]';
font_end_res = {}; font_start_res = {}
for i in ('*', '~~'):
	font_end_res[i] = re.compile(ere_text.replace('X', re.escape(i)))
	font_start_res[i] = re.compile(sre_text.replace('X', re.escape(i)))
# '_' has relaxed rules; anything qualifies. This is because _ affects
# text spacing, so I have been known to write things like '_ \\ _' and
# want it to come out JUST LIKE THAT. Arguably I should have used ((..))
# for this, but I didn't so that's life and we cope.	- cks
font_end_res['_'] = font_start_res['_'] = re.compile("(.*?)_")

def inline_font(rend, style, text):
	if style == '*':
		hstyle = 'em'
	elif style == '~~':
		hstyle = 'strong'
	elif style == '_':
		hstyle = 'code'     # why not tt?  What's the difference?
	# Doubled characters would normally produce empty HTML bits, which
	# at least HTML tidy complains about. Rather than have yet another
	# way of quoting things, we choose to make them produce themselves.
	elif style in ('**', '__', '~~~~'):
		rend.result.append(style)
		return

	# If this style is blocked by a nesting element, we must ignore
	# this style. Consider '*test [[case* http://google.com/]]'.
	if rend.blocked_style(hstyle):
		rend.result.append(style)
		return

	# Is this style currently disabled?
	if style in rend.disabledStyles:
		rend.result.append(style)
		return

	# Closing things is the easy case, so we handle it and then
	# bail.
	offtag = end_entity[hstyle]
	if offtag in rend.inlineEndStack:
		s = rend.inlineEndStack.pop(0)
		while s != offtag:
			rend.result.append(s)
			s = rend.inlineEndStack.pop(0)
		rend.result.append(s)
		return

	# We insist that start tags be followed by non-whitespace.
	# Otherwise, they're just themselves.
	if not text or (style != '_' and text[0] in string.whitespace):
		rend.result.append(style)
	else:
		# The complicated case; basically, we insist on minimal
		# complete spans. This means that:
		# a) there must be a valid ending tag for this font start
		#    (valid end tags have non-whitespace before them)
		# b) there must not be any other valid font starts between
		#    us and the first end tag found.
		mo = font_end_res[style].search(text)
		if not mo or font_start_res[style].search(mo.group(1)):
			rend.result.append(style)
		else:
			rend.inlineEndStack.insert(0, offtag)
			rend.result.append(start_entity[hstyle])

# if we fail to check for _ nesting like this, then our
# output looks wrong after going through html tidy
def inline_code(rend, txt):
	alreadycode = (end_entity['code'] in rend.inlineEndStack)
	if not alreadycode:
		rend.result.append(start_entity['code'])
	rend.handle_plaintext(txt)
	if not alreadycode:
		rend.result.append(end_entity['code'])

def inline_macro(rend, txt):
	if not rend.macro(txt):
		rend.result.append('{{')
		rend.handle_plaintext(txt)
		rend.result.append('}}')

def inline_http(rend, txt):
	linkend = rend.makelinkstart(txt)
	rend.handle_plaintext(txt)
	if linkend:
		rend.result.append('</a>')

def inline_brlink(rend, txt):
	if not rend.brlink(txt):
		rend.result.append('[[')
		rend.handle_text("text", txt)
		rend.result.append(']]')

def inline_br(rend, txt):
	__pychecker__ = "no-argsused"
	rend.result.append("<br>\n")

# WikiLinks only activate if they are actually live pages.
# WikiLinks are either absolute (preferred) or in the same
# directory. The latter turns out to be what I want about
# 99% of the time.
def inline_wikiword(rend, link):
	# Shortcut us if we aren't resolving links.
	if rend.options & NOLINKS:
		rend.result.append(link)
		return

	# CHECKME
	# apparently all the link-finding stuff below can't find
	# words with slashes on the end, so we do the link search
	# without the slash
	linkc = link
	if linkc[-1] == '/':
		linkc = linkc[:-1]

	# We try first as an absolute path, then as a relative
	# path, then finally as something in our alias area.
	url = False
	cp = None
	# Check the cache to see if we have a result already.
	if linkc in rend.wikiwordCache:
		url = rend.wikiwordCache[linkc]
	else:
		for cp in (rend.mod.get_page(linkc),
			   rend.mod.get_page_relname(rend.ctx.page, linkc),
			   rend.mod.get_alias_page(linkc),
			   rend.mod.get_page_paths(rend.searchPath, linkc)):
			if cp and cp.exists():
				break
		if cp:
			url = rend.page_url(cp)
		rend.wikiwordCache[linkc] = url

	if url:
		linkend = rend.makelinkstart(url)
		rend.result.append(link)
		if linkend:
			rend.result.append('</a>')
	else:
		rend.result.append(link)

def inline_control(rend, txt):
	if len(txt) > 1:
		pl = "characters"
	else:
		pl = "character"
	rend.result.append("{bad %s}" % pl)

def double_paren_pat(opn, cls):
	(bo, bc) = ("\\" + opn, "\\" + cls)
	(do, dc) = (2 * bo, 2 * bc)
	notc = '[^%s]' % bc
	return ("%sX(?:%s|%s%s)+Y%s" % (do, notc, bc, notc, dc))

# Bad things happen if the first character subsets of two
# inline patterns overlap, so avoid doing that.
prelinestuff = [
	['_*~', r'X(?:__|\*\*|~~~~|_|\*|~~)Y', inline_font],
	['(', double_paren_pat('(',')'), inline_code],
	# ``...'' to quote text.
	["`", double_paren_pat("`", "'"), inline_plaintext],
	# XXX: this currently matches just '\\<newline>', not the
	# documented ' \\<newline>'. For now I will pass.
	# (To solve this we could use a '(<= )' lookbehind assertion,
	# but then we would have to switch the entire big loop to using
	# and keeping offsets into the text.)
	['\\', 'XY' + ('\\' * 4) + '(?:$|\n)', inline_br],
	#['<>&"\'', 'X[\'"<>&]+Y', inline_plaintext],
	['<&', 'X[<&]+Y', inline_plaintext],
	# We need to handle (invalid) control characters somehow.
	[cc_chars(), 'X%s+Y' % cc_pat(), inline_control],
	['!', r'!X(?:\[\[|\{\{|\(\(|https?://|``)Y', inline_plaintext],
	# bang marks the end of "limited" text subset
	['{', double_paren_pat('{','}'), inline_macro],
	['[', double_paren_pat('[',']'), inline_brlink],
	['h', r'\bXhttps?://[A-Za-z0-9](?:(?![.,;\)"\']*(?:\s|$)).)*Y', inline_http],
#	This regexp is *so* last Monday:
#	['A-Z', r'\bX[A-Z][a-z0-9./]*[A-Z][A-Za-z0-9./]*[A-Za-z0-9/]Y', inline_wikiword],
	['A-Z', r'(?<!/)\bX[A-Z][a-z0-9./]*[A-Z][A-Za-z0-9./]*[A-Za-z0-9/]Y', inline_wikiword],
	['/', r'(?<![A-Za-z0-9])X/[A-Z][a-z0-9./]*[A-Z][A-Za-z0-9./]*[A-Za-z0-9/]Y', inline_wikiword],
	]

textrehash = {}
textcodehash = {}
ltextrehash = None
plainre = []
bangplainre = None

# Done to contain temporary variables, because heaven forbid we have
# actual nested scopes aside from function definitions.
def maketexttables():
	# probably don't need to declare textrehash and textcodehash
	global textrehash, textcodehash, ltextrehash, plainre, bangplainre
	firstcharcollection = []
	firstcharbitre = re.compile('(.)(?:-(.))?')
	for linespec in prelinestuff:
		(firstchar, lre, cdref) = linespec
		isbang = (firstchar == '!')
		plainrebit = lre.replace('X','').replace('Y','')
		lre = lre.replace('X','(').replace('Y',')')
		lre = re.compile(lre)
		mo = firstcharbitre.match(firstchar)
		while (mo):
			keys = (ord(mo.group(1)),)
			if mo.group(2):
				keys = range(ord(mo.group(1)), ord(mo.group(2))+1)
			for key in keys:
				if not chr(key).isalnum():
					firstcharcollection.append('\\')
				firstcharcollection.append(chr(key))
				textrehash[key] = lre
				textcodehash[key] = cdref
			firstchar = firstchar[mo.end(0):]
			mo = firstcharbitre.match(firstchar)
		plainre.append(plainrebit)
		if isbang:
			ltextrehash = dict([(k,v) for (k,v) in textrehash.iteritems()])
			bangplainre = '((?:[^%s]+|(?!%s).)+)' % \
						  (''.join(firstcharcollection), '|'.join(plainre))
	plainre = '((?:[^%s]+|(?!%s).)+)' % \
				(''.join(firstcharcollection), '|'.join(plainre))
	plainre = re.compile(plainre)
	bangplainre = re.compile(bangplainre)

maketexttables()
del maketexttables   # and all our temporaries go away
del prelinestuff
del double_paren_pat

# The pattern used to split potential stop words to determine how to
# make them go.
# This is not deducable from bangplainre, because we want only style and
# other special characters.
stopw_char_re = re.compile(r"(.*?)([_~*({\[!<&])(.*)")
class StopWord:
	def __init__(self, pref, o, suf):
		self.pref = pref
		self.o = o
		self.suf = suf
		self.rest = chr(o) + suf

# Canonicalize link text for our link abbreviations.
def canon_ltext(ltext):
	return " ".join(ltext.split())

def list_para_start(tok):
	if tok[3].group(1) == '+':
		return "para"
	else:
		return "ptext"

# used inside makelinkstart to detect urls that need absolutin'
absurlre = re.compile('[a-zA-Z0-9]+:')

# We use a class to contain rendering because it simplifies the job of
# holding related data all accessible.
class WikiRend:
	def __init__(self, data, context, options = None):
		self.data = data
		self.ctx = context
		self.mod = context.model
		self.web = context.web
		self.result = []
		self.blockEndStack = []
		self.inlineEndStack = []
		self.tokqueue = []
		self.features = []
		self.linkNames = {}
		self.linkUrls = {}
		self.wikiwordCache = {}
		self.abbrCache = {}
		self.imgCache = {}
		self.blockedStyles = []
		self.textTitle = None
		self.useLists = True   # macros refer to this
		self.usePageTitles = False
		self.hasComplex = False
		self.searchPath = []
		self.disabledStyles = {}
		self.textSubs = {}
		if options is None:
			self.options = 0
		else:
			self.options = options

		# stopwords are complicated.
		# stopWords indexes words -> sw objects.
		# sw_index goes from special character to the list of
		# sw for that character.
		self.stopWords = {}
		self.sw_index = {}
		self.stopPerm = {}
		self.sw_cache = {}
		# Load global stopwords, which cannot be removed.
		if 'literal-words' in context.cfg:
			for w in context.cfg['literal-words']:
				self.add_stopword(w, True)

	def render(self, options = None):
		if options:
			self.options = options
		try:
			self.result.append(wikitext_start)
			self.run()
			self.result.append(wikitext_end)
		except macros.ReturnNothing:
			self.result = []
			self.ctx.unrel_time()
			self.textTitle = None
			# This one is complicated.
			# Consider: a readable file, in a directory
			# with an __access that has {{Restricted:user}}
			# and {{CanComment:user}}, and a higher level
			# __access file that would allow commenting.
			# The intention is to disable access to everything
			# in the directory, except this file, and to allow
			# commenting on nothing.
			# If we zapped features narrowly, we would kill
			# the CanComment. (Which must be before the
			# Restricted, whee.)
			self.features.append('restricted')
		if self.options & set_features_option:
			for f in self.features:
				self.ctx.addfeature(f)
		if self.hasComplex:
			self.ctx.addfeature('indirect-macros')
		if self.options & set_title_option and self.textTitle:
			self.ctx.setvar(":wikitext:title", self.textTitle)
		if self.searchPath:
			self.ctx.setvar(":wikitext:search", self.searchPath)
		return ''.join(self.result)

	def pull(self):
		if self.tokqueue:
			return self.tokqueue.pop(0)
		if not self.data:
			return None
		tl = self.data.split('\n', 1)
		if len(tl) > 1:
			self.data = tl[1]
		else:
			self.data = None
		self.currentLineNumber += 1
		return getTokFromLine(tl[0], 0, self.currentLineNumber)

	def pushBack(self, tok, *othertoks):
		if othertoks:
			self.tokqueue[0:0] = othertoks
		if tok:
			self.tokqueue.insert(0, tok)

	def run(self):
		if is_plaintext(self.data):
			tl = self.data.split('\n', 1)
			if len(tl) > 1 and tl[1]:
				self.result.append('<pre>')
				self.handle_plaintext(tl[1])
				self.result.append('</pre>')
			return
		sr = search_dirs(self.data)
		# FIXME: handle pragmas better. Really pragmas should
		# return a pragma result + rest of data blob.
		if sr:
			self.searchPath = sr
			_, self.data = self.data.split("\n", 1)
		self.currentLineNumber = 0
		filters = WikiRend.filter_routines
		try:
			x = self.pull()
			while x:
				filters[x[0]](self, x)
				x = self.pull()
				if self.options & TITLEONLY and \
				   self.currentLineNumber >= 2:
					# Time to get out.
					break
		except macros.CutShort:
			pass
		self.result.extend(self.inlineEndStack)
		self.result.extend(self.blockEndStack)
		self.inlineEndStack = []
		self.blockEndStack = []

	filter_routines = {}

	def blank_handler(self, tok):
		__pychecker__ = "no-argsused"
		# Generating newlines in the output for blank lines in
		# the source makes the generated HTML look much nicer.
		self.result.append("\n")
	filter_routines['blank'] = blank_handler

	def starsep_handler(self, tok):
		__pychecker__ = "no-argsused"
		self.result.append('<p align="center">* * *</p>\n')
	filter_routines['starsep'] = starsep_handler

	def hr_handler(self, tok):
		__pychecker__ = "no-argsused"
		self.result.append('<hr>\n')
	filter_routines['hr'] = hr_handler

	def header_handler(self, tok):
		hlevel = min(len(tok[3].group(1)), 6)
		htext = tok[3].group(2)
		hdtag = 'h%d' % hlevel
		self.handle_begin('begin', hdtag)
		self.handle_text('text', htext)
		self.handle_end('end', hdtag)
	filter_routines['header'] = header_handler

	# header on the first line
	def header1_handler(self, tok):
		spos = len(self.result) + 1
		self.header_handler(tok)
		self.textTitle = ''.join(self.result[spos:-1])
	filter_routines['header1'] = header1_handler

	def table_handler_inner(self, tok, type):
		self.handle_begin('begin', type)
		while tok and tok[0] in ('table', 'horitable'):
			rowtext = [tok[3].group(1)]
			tok = self.pull()
			while tok and tok[0] == 'indented':
				ntext = tok[3].group(3)
				rowtext.append('\n')
				rowtext.append(ntext)
				tok = self.pull()
			self.handle_begin('begin', 'tr')
			rowdata = table_cell_boundary_re.split(''.join(rowtext))
			if rowdata and not rowdata[-1].strip():
				rowdata.pop()
			for tbltxt in rowdata:
				ent = 'td'
				tbltxt = tbltxt.strip()
				if numeric_td_match_re.match(tbltxt):
					ent = 'ntd'
				self.handle_begin('begin', ent)
				self.handle_text('text', tbltxt)
				self.handle_end('end', ent)
			self.handle_end('end', 'tr')
		self.handle_end('end', type)
		self.pushBack(tok)
	def table_handler(self, tok):
		self.table_handler_inner(tok, "table")
	def horitable_handler(self, tok):
		self.table_handler_inner(tok, "horitable")
	filter_routines['table'] = table_handler
	filter_routines['horitable'] = horitable_handler

	def indented_handler(self, tok):
		# CHECKME
		# This mitigates the tab vs. space indentation issue
		# Basically, if any line in a <pre> block begins
		# with a space, then any line beginning with a tab
		# is treated as though it began with a space-tab
		# A true solution would replace the group(2) bit with
		# ' ' * (expandedLength(8,0,group(1)+group(2))-1)
		# to get the old behavior, comment out each line
		# marked with "#s-t trick"
		#hasspace = (tok[3].group(1) == ' ')	# s-t trick
		pretext = ['\n', tok[3].group(2), tok[3].group(3)]
		# The initial \n isn't displayed, and it means html tidy doesn't
		# screw up the tabs.
		tok = self.pull()
		while (tok and tok[0] == 'indented'):
			pretext.append("\n")
			#if tok[3].group(1) == '\t':	#s-t trick
			#	pretext.append(True)	#s-t trick
			#else:				#s-t trick
			#	hasspace = True		#s-t trick
			pretext.append(tok[3].group(2))
			pretext.append(tok[3].group(3))
			tok = self.pull()
			# merge adjacent <pre> regions separated by
			# blank lines, because this is what *should*
			# happen.
			if tok and tok[0] == 'blank':
				ntok = self.pull()
				if ntok and ntok[0] == 'indented':
					pretext.append("\n")
					tok = ntok
				else:
					self.pushBack(ntok)
		self.pushBack(tok)
		#pretextmp = {hasspace: '\t'}		#s-t trick
		#pretext = [pretextmp.get(x,x) for x in pretext]	#s-t trick
		pretext.append("\n")		# XXX: cks added.
		self.handle_begin('begin', 'pre')
		self.handle_plaintext(''.join(pretext))
		self.handle_end('end', 'pre')
	filter_routines['indented'] = indented_handler

	def quote_handler(self, tok):
		qlevel = 0
		rettoks = []
		while (tok and tok[0] == 'quote'):
			qstr = tok[3].group(1)
			newqlevel = ( len(qstr) + 1 ) / 2
			while (newqlevel > qlevel):
				rettoks.append( ('begin', 'blockquote') )
				qlevel += 1
			while (newqlevel < qlevel):
				rettoks.append( ('end', 'blockquote') )
				qlevel -= 1
			rettoks.append(tok[4])
			tok = self.pull()
		self.pushBack(tok)
		while (0 < qlevel):
			rettoks.append( ('end', 'blockquote') )
			qlevel -= 1
		self.pushBack(*rettoks)
	filter_routines['quote'] = quote_handler

	# This handles quoted sections that are created by indentation,
	# not by > > levels. Currently this is available only in lists.
	def iquote_handler(self, tok):
		qlevel = 0
		ilevel = [0]
		rettoks = []
		while (tok and tok[0] == 'iquote'):
			subtok = tok[1]
			qlevel = subtok[2]
			subtype = subtok[0]
			if qlevel > ilevel[-1]:
				ilevel.append(qlevel)
				rettoks.append(('nl', None))
				rettoks.append(('begin', 'blockquote'))
			elif qlevel < ilevel[-1]:
				while qlevel < ilevel[-1]:
					ilevel.pop()
					rettoks.append(('end', 'blockquote'))
			#rettoks.append(('ptext', tok[1][1]))
			# In order to handle embedded block constructs like
			# lists, we must leave the tokens mostly intact.
			# However, we turn 'p' into 'ptext' to avoid
			# introducing extra <p>s.
			if subtype == 'p':
				subtok = ('ptext',) + subtok[1:]
			rettoks.append(subtok)
			tok = self.pull()
		self.pushBack(tok)
		while len(ilevel) > 1:
			ilevel.pop()
			rettoks.append(('end', 'blockquote'))
		self.pushBack(*rettoks)
	filter_routines['iquote'] = iquote_handler

	def innerlist_handler(self, tok):
		# CHECKME
		# This for those "direct to second-level" list thingys
		# that break html validation.  If you don't like
		# the way this looks, you can change the start/end tags
		# by adjusting start_entity['innerlist'] above
		rettoks = [('begin','innerlist')]
		while tok:
			if tok[0] == 'innerlist':
				rettoks.append(tok[4])
			elif tok[0] == 'indented':
				rettoks.append(tok)
			else:
				break
			tok = self.pull()
		self.pushBack(tok)
		rettoks.append( ('end', 'innerlist') )
		self.pushBack( *rettoks )
	filter_routines['innerlist'] = innerlist_handler

	def list_handler(self, tok):
		def cont_list_cond(tok):
			if not tok:
				return False
			if tok[0] not in ('indented', 'ol', 'ul', 'dl'):
				return False
			if tok[0] == 'indented' and \
			   tok[4][2] > lowestsubindent:
				return False
			if tok[0] == 'indented' and \
			   tok[4][2] > baseindent and not isiquote:
				return False
			return True
		ltype = tok[0]
		rettoks = [ ('begin', ltype) ]
		isfirst = True
		isiquote = False
		lowestsubindent = 99999   # infinity, or close enough
		baseindent = tok[2] + 2
		while tok:
			ntype = tok[0]
			if ntype == 'indented':
				subtok = tok[4]
				subindent = subtok[2]
				subntype = subtok[0]
				if subindent > lowestsubindent:
					rettoks.append(tok)
				elif isiquote and subindent > baseindent + 2:
					# must be before indented list types,
					# otherwise they swallow sublists
					# that are in indented blockquotes.
					# except that we don't want to swallow
					# an extremely indented start of list
					# if it is the start of an extremely
					# indented block, nngh...
					# FIXME: code smell.
					rettoks.append( ('iquote', subtok) )
				elif subntype == 'ol' or subntype == 'ul' or \
					 subntype == 'dl': # Change this to allow quotes in lists
					lowestsubindent = subindent
					rettoks.append(subtok)
				elif subindent > baseindent + 2:
					rettoks.append(('iquote', subtok))
					isiquote = True
				else:
					rettoks.append( ('ptext', subtok[1]) )
					lowestsubindent = 99999
					isiquote = False
			elif ntype == 'innerlist':
				subtok = tok[4]
				rettoks.append(subtok)
				lowestsubindent = subtok[2]
				isiquote = False
			elif ntype == ltype:
				ptype = list_para_start(tok)
				if ntype == 'dl':
					if not isfirst:
						rettoks.append( ('end', 'dd') )
					rettoks.append( ('begin', 'dt') )
					t1 = tok[3].group(3)
					if t1:
						rettoks.append( ('ptext', t1) )
					rettoks.append( ('end', 'dt') )
					rettoks.append( ('begin', 'dd') )
					t2 = tok[3].group(5)
					if t2:
						rettoks.append( ('ptext', t2) )
				else:
					if not isfirst:
						rettoks.append( ('end', 'li') )
					rettoks.append( ('begin', 'li') )
					t1 = tok[3].group(3)
					if t1:
						rettoks.append( (ptype, t1) )
				isfirst = False
				lowestsubindent = 99999
				isiquote = False
			elif ntype == 'blank':
				ntok = self.pull()
				if cont_list_cond(ntok):
					# We swallow the blank line and
					# turn it into a <p>, and otherwise
					# fall through to general processing
					rettoks.append(('insert', 'p'))
					self.pushBack(ntok)
				elif ntok and ntok[0] == 'indented' and \
				     ntok[4][2] > lowestsubindent:
					# handle our recursive indenting
					# handling.
					rettoks.append(tok)
					rettoks.append(ntok)
				else:
					if ntok:
						self.pushBack(ntok)
					self.pushBack(tok)
					break
			else:
				self.pushBack(tok)
				break
			tok = self.pull()
		if ltype == 'dl':  # WHAT THE HELL, PYTHON?  Where's my ?: operator?
			rettoks.append( ('end', 'dd') )
		else:
			rettoks.append( ('end', 'li') )
		rettoks.append( ('end', ltype) )
		self.pushBack( *rettoks )
	filter_routines['ol'] = list_handler
	filter_routines['ul'] = list_handler
	filter_routines['dl'] = list_handler

	# This code could be written as just the while loop, but
	# special-cases the very common case of just a single p by itself
	def p_handler(self, tok):
		ntxt = tok[3].group(1)
		tok = self.pull()
		if tok and tok[0] == 'p':
			txt = [ntxt, tok[3].group(1)]
			tok = self.pull()
			while tok and tok[0] == 'p':
				txt.append(tok[3].group(1))
				tok = self.pull()
			self.pushBack(tok)
			ntxt = '\n'.join(txt)
		else:
			self.pushBack(tok)
		self.handle_begin('begin', 'p')
		self.handle_text('text', ntxt)
		self.handle_end('end', 'p')
	filter_routines['p'] = p_handler

	def ptext_handler(self, tok):
		ntxt = tok[1]
		tok = self.pull()
		if tok and tok[0] == 'ptext':
			txt = [ntxt, tok[1]]
			tok = self.pull()
			while tok and tok[0] == 'ptext':
				txt.append(tok[1])
				tok = self.pull()
			self.pushBack(tok)
			ntxt = '\n'.join(txt)
		else:
			self.pushBack(tok)
		self.handle_text('text', ntxt)
	filter_routines['ptext'] = ptext_handler

	def para_handler(self, tok):
		txt = [tok[1],]
		tok = self.pull()
		while tok and tok[0] in ('para', 'ptext'):
			txt.append(tok[1])
			tok = self.pull()
		self.pushBack(tok)
		self.handle_begin('begin', 'p')
		self.handle_text('text', '\n'.join(txt))
		self.handle_end('end', 'p')
	filter_routines['para'] = para_handler

	# The pushback error case is difficult to handle, because
	# technically we should merge with adjacent lines to make
	# a single paragraph. However, we punt on that, so we get
	# separate paragraphs.
	def procnote_handler(self, tok):
		txt = tok[3].group(1)
		n = txt.split()
		while n:
			pn_name = n.pop(0)
			pn = macros.get_pnote(pn_name)
			if not pn:
				self.pushBack(('para', tok[1], tok[2]))
				return
			ac = macros.pnote_args(pn_name)
			if ac > len(n):
				self.pushBack(('para', tok[1], tok[2]))
				return
			args = n[:ac]
			if not pn(self, args):
				self.pushBack(('para', tok[1], tok[2]))
				return
			n = n[ac:]
	filter_routines['procnote'] = procnote_handler

	# When modifying these, remember that header1 handler depends
	# on the fact that the start tag is the first thing and that
	# the end tag is the last appended
	def handle_begin(self, ignore, btype):
		__pychecker__ = "no-argsused"
		self.result.append(start_entity[btype])
		self.blockEndStack.insert(0, end_entity[btype])

	def handle_end(self, ignore, btype):
		__pychecker__ = "no-argsused"
		if self.inlineEndStack:
			self.result.extend(self.inlineEndStack)
			self.inlineEndStack = []
		sofftag = self.blockEndStack.pop(0)
		if sofftag != end_entity[btype]:
			raise derrors.IntErr, "Programming error; expected %s got %s" % (sofftag, end_entity[btype])

		# Optimization hack: remove certain empty elements.
		# (Other empty elements, like <td>, have semantic meaning.)
		if (self.result[-1] == start_entity[btype]) and \
		   btype in ('p', 'blockquote'):
			self.result.pop()
		else:
			self.result.append(sofftag + "\n")

	def begin_handler(self, tok):
		self.handle_begin( *tok )
	filter_routines['begin'] = begin_handler
	def end_handler(self, tok):
		self.handle_end( *tok )
	filter_routines['end'] = end_handler

	# Insert an unmatched block-level entity.
	def insert_handler(self, tok):
		if self.inlineEndStack:
			self.result.extend(self.inlineEndStack)
			self.inlineEndStack = []
		self.result.append(start_entity[tok[1]])
		self.result.append("\n")
	filter_routines['insert'] = insert_handler

	# Insert a newline. In theory this handler should be unnecessary.
	# Theory is a wonderful thing.
	def nl_handler(self, tok):
		__pychecker__ = "no-argsused"
		self.result.append("\n")
	filter_routines['nl'] = nl_handler
	
	handle_plaintext = inline_plaintext
	def plaintext_handler(self, tok):
		self.handle_plaintext(tok[1])
	filter_routines['plaintext'] = plaintext_handler

	def set_style_barrier(self):
		self.blockedStyles.append(self.inlineEndStack[:])
	def clear_style_barrier(self):
		self.blockedStyles.pop()
	def blocked_style(self, style):
		return self.blockedStyles and \
		       end_entity[style] in self.blockedStyles[-1]

	def handle_text(self, typ, text):
		_textrehash = textrehash
		_plainre = plainre
		_swidx = self.sw_index
		_fsw = self.find_stopword
		if typ == 'ltext':
			_textrehash = ltextrehash
			_plainre = bangplainre
			_swidx = {}
		# Perform any active substitutions.
		if self.textSubs:
			for a in self.textSubs.values():
				# We must use a lambda to suppress
				# backslash processing in the
				# replacement text. (re.escape doesn't
				# do it, sigh.)
				text = a[0].sub(a[1], text)
		while text:
			o = ord(text[0])
			mo = None
			if o in _textrehash:
				mo = _textrehash[o].match(text)
			if mo:
				# we only need to eat a stopword if we matched
				# something to start with.
				if o in _swidx:
					sw = _fsw(o, text)
					if sw:
						text = text[len(sw.rest):]
						inline_plaintext(self, sw.rest)
						continue
				text = text[mo.end(0):]
				routine = textcodehash[o]
				if routine == inline_font:
					routine(self, mo.group(1), text)
				else:
					routine(self, mo.group(1))
			else:
				mo = _plainre.match(text)
				if not mo:
					raise derrors.IntErr, "Programming error; nothing matched '%s' in '%s'" % (_plainre.pattern, text)
				text = text[mo.end(0):]
				self.result.append(mo.group(1))

	# Stop words
	def add_stopword(self, word, permanent = False):
		# otherwise things explode messily.
		if word in self.stopWords:
			return
		
		# if there are no special characters, we can immediately
		# bail.
		mo = stopw_char_re.match(word)
		if not mo:
			return
		pref, o, suf = mo.group(1), ord(mo.group(2)), mo.group(3)
		# must have at either a prefix or a suffix; otherwise,
		# nice try at turning off a special character, but no.
		if not (pref or suf):
			return

		sw = StopWord(pref, o, suf)
		self._sw_add(word, sw, permanent)

	def _sw_add(self, word, sw, permanent):
		o = sw.o
		if o not in self.sw_index:
			self.sw_index[o] = []
		self.sw_index[o].append(sw)
		self.stopWords[word] = sw
		if o in self.sw_cache:
			del self.sw_cache[o]
		if permanent:
			self.stopPerm[word] = sw

	# issue: needs performance tuning. heck, needs performance
	# analysis.

	# For each special character, we maintain a set of characters
	# at the end of all prefixes and a set of characters at the
	# start of all suffixes, as well as a flag to say whether any
	# stopwords have an empty prefix or suffix.
	# This lets us check prev[-1] and text[1] quickly to see if
	# we are guaranteed not to match a stopword (one or both of
	# them is a character that does not occur at the end of any
	# prefix or at the start of any suffix).
	# The existence of empty prefixes and suffixes complicates
	# this slightly. Since we insist that you must have at least
	# one of them, for special characters with such stopwords we
	# can only reject if neither prefix nor suffix matches, not if
	# either doesn't match.
	def _build_cache(self, o):
		cv = self.sw_index[o]
		stc = {}
		enc = {}
		both = True
		for sw in cv:
			if not (sw.pref and sw.suf):
				both = False
			if sw.pref:
				c = ord(sw.pref[-1])
				enc[c] = True
			if sw.suf:
				c = ord(sw.suf[0])
				stc[c] = True
		self.sw_cache[o] = (both, enc, stc)

	def find_stopword(self, o, text):
		if o not in self.sw_cache:
			self._build_cache(o)
		# we *always* have a result[-1], because all text has to
		# be in some container and the start tag for the container
		# will be self.result[-1].
		l = self.result[-1]

		# Quickly check the character immediately before us and
		# the character immediately after us.
		both, enc, stc = self.sw_cache[o]
		ec = ord(l[-1])
		if len(text) > 1:
			sc = ord(text[1])
		else:
			sc = None
		# if both is true, then both sc and ec must be in their
		# respective matchers.
		# if both is not true, at least one of them must be in
		# their matcher.
		if both and not (sc in stc and ec in enc):
			return None
		elif (sc not in stc and ec not in enc):
			return None

		# Find the sucker for real.
		for sw in self.sw_index[o]:
			if sw.pref and not l.endswith(sw.pref):
				continue
			if sw.suf and not text.startswith(sw.rest):
				continue
			return sw
		return None

	# Clearing all stopwords does not remove global (aka permanent)
	# stopwords; it just clears page-defined ones.
	def clear_stopwords(self):
		self.stopWords.clear()
		# this does not detach the cached reference to sw_index
		# that handle_text() may be keeping.
		self.sw_index.clear()
		for w in self.stopPerm.iterkeys():
			self._sw_add(w, self.stopPerm[w], False)
		self.sw_cache.clear()
	# You *can* explicitly clear a global stopword. We assume that
	# you know what you're doing.
	def clear_stopword(self, word):
		if word not in self.stopWords:
			return
		sw = self.stopWords[word]
		del self.stopWords[word]
		cv = self.sw_index[sw.c]
		cv.remove(sw)
		if not cv:
			del self.sw_index[sw.c]
		if word in self.stopPerm:
			del self.stopPerm[word]
		if sw.o in self.sw_cache:
			del self.sw_cache[sw.o]

	# ---
	# We now rely on each thing making links to close them
	def is_absurl(self, url):
		return bool(absurlre.match(url)) and \
		       not url.lower().startswith("javascript:")
	# Macros don't have access to flags like ABSLINKS; besides, we
	# might as well put this in one place.
	def canon_url(self, url):
		if self.options & ABSLINKS and \
		   not self.is_absurl(url):
			return self.ctx.web.uri_from_url(url, self.ctx)
		else:
			return url
	def makelinkstart(self, dest):
		if self.options & NOLINKS:
			return False
		else:
			dest = self.canon_url(dest)

			# I vastly prefer % escapes in links to entity
			# escapes. However, we must escape & as an entity,
			# not as a % thing. " can be %-escaped.
			# I believe that everything else can be left as-is,
			# although at some point I will have to run it all
			# through a validator.
			#dest = dest.replace('&', '&amp;').replace('"', '&quot;')
			#dest = dest.replace('<', '&lt;').replace('>', '&gt;')
			dest = dest.replace('&', '&amp;')
			dest = dest.replace('"', '%22')
			# Debateable, but let's be nice to simplistic
			# programs.
			dest = dest.replace('>', '%3E').replace(' ', '%20')
			nofollow = ''
			if self.options & NOFOLLOW:
				nofollow = ' rel="nofollow"'
			self.result.append('<a href="%s"%s>' % (dest, nofollow))
			return True

	# Split apart a [[...|...]] thing and verify quality.
	def splitbrlink(self, link):
		# We know that the link has a | in it or we wouldn't
		# be here, so this must yield two elements.
		ltext, link = link.split("|", 1)
		
		# I keep writing [[...||...]], so cope with this.
		if link and link[0] == '|':
			link = link[1:]

		ltext = ltext.strip()
		link = link.strip()

		# Trivial case: nuke totally empty things.
		if not ltext and not link:
			return None

		# We canonicalize link text to remove newlines and
		# suchlike.
		lctext = canon_ltext(ltext)

		# We maintain a cache of link names / link values, so
		# that we can abbreviate things.
		if link and lctext:
			self.linkNames[link] = lctext
			self.linkUrls[lctext] = link
		elif link in self.linkNames:
			ltext = self.linkNames[link]
		elif lctext in self.linkUrls:
			link = self.linkUrls[lctext]

		# If someone writes [[|<link>]] with no cached name
		# for the link, the best we can do is pretend that
		# the link name is the same as the link.
		if not ltext:
			return (link, link)

		# No link == person is hijacking to create escaped text.
		# So now we know we're good.
		return (ltext, link)
	
	def splitbrspace(self, link):
		# Allow abbreviations right off the bat:
		l2 = canon_ltext(link)
		if l2 in self.linkUrls:
			return (link, self.linkUrls[l2])
		# (linkNames makes no sense, as a link URL is highly
		#  unlikely to have any spaces in it.)
		mo = lastspace_re.match(link)
		if not mo:
			raise derrors.IntErr, "Chris screwed up splitbrspace's regexp: |%s|" % link
		ltext, link = mo.group(1), mo.group(2)
		l2 = canon_ltext(ltext)
		self.linkNames[link] = ltext
		self.linkUrls[l2] = link
		return (ltext, link)

	# URL for a page, chasing one level of redirects.
	# Issue: do we use .nurl() or .url()? We use .nurl() throughout
	# at the moment, so.
	def page_url(self, page, internalOnly = False):
		url = self.ctx.nurl(page)
		# Is the target a redirection, and if so does the
		# target of the redirection exist?
		res = page.redirect_target()
		if res:
			if res[0] != 'page' and not internalOnly:
				url = res[1]
			elif res[1] and res[1].exists():
				url = self.ctx.nurl(res[1])
		# We decline to walk multiple steps of redirections,
		# because then we'd have to figure out if we were
		# looping.
		return url		

	# [[...]] format links open the question of unterminated links.
	# wikirend forces them to be on the same line, and if they are
	# not it writes the text literally.
	def brlink(self, txt):
		"""Does a [[]] bracketed link.

		As with macro(), returns True if it took care of things, false otherwise."""
		link = txt.strip()
		if not link:
			return False

		# We allow the canonical '|' to separate link text
		# and link name.
		lname = None
		if '|' in link:
			lp = self.splitbrlink(link)

			if not lp:
				return False
			# We're being hijacked to make plain text.
			elif lp and not lp[1]:
				self.handle_plaintext(lp[0])
				return True
			# Otherwise, we have a link and link text.
			lname, link = lp
		elif ' ' in link or '\n' in link:
			lp = self.splitbrspace(link)
			if not lp:
				return False
			lname, link = lp
		elif not self.is_absurl(link) and \
		     not (link and link[0] == '<' and link[-1] == '>'):
			# Try to find it as a previously memoized thing,
			# *provided* that it doesn't exist.
			npage = self.mod.get_page_relname(self.ctx.page,
							  link)
			if (not npage or not npage.exists()) and \
			   link in self.linkUrls:
				lname = link
				link = self.linkUrls[lname]

		# Process ever onwards.
		if self.is_absurl(link):
			url = link
			deflname = link
		elif link[0] == '<' and link[-1] == '>':
			url = link[1:-1]
			deflname = url
		else:
			npage = self.mod.get_page_relname(self.ctx.page, link)
			if self.searchPath and \
			   (not npage or not npage.exists()):
				np = self.mod.get_page_paths(self.searchPath,
							     link)
				if np:
					npage = np
			if npage:
				url = self.page_url(npage)
				deflname = npage.name
			else:
				deflname = link
				url = link
		if not lname:
			lname = deflname
		linkend = self.makelinkstart(url)
		self.set_style_barrier()
		try:
			self.handle_text("ltext", lname)
		finally:
			if linkend:
				self.result.append('</a>')
			self.clear_style_barrier()
		return True

	#
	# -----
	# This generates magic macros that are used for things such as
	# 'recent changes' and category searches.
	# Macros are invoked as {{macro[:param:param...]}}. Non-blank
	# parameters are passed to the macro itself. Macros are
	# strongly encouraged to be as accepting as possible in their
	# arguments.
	def allowedmacro(self, macro):
		# Certain macros are actually text formatting instead
		# of real macros, and are always allowed.
		if macros.text_macro(macro):
			return True
		# check for 'only this' stuff
		if self.options & SOMEMACROS:
			return bool(self.options &
				    macro_perms.get(macro, 0))
		# Do we allow page rendering to be cut short?
		# If not, we swallow the macro.
		elif macro in macro_block_perms and \
		   not (self.options & macro_block_perms[macro]):
			return False
		else:
			return True

	def macro(self, txt):
		"""Process a single macro.

		Returns true if it's taken care of things. """
		mac = txt.strip()
		if not mac:
			return False
		ml = [x.strip() for x in mac.split(':')]
		if not ml[0]:
			return False
		# Drop empty arguments
		mla = [x for x in ml[1:] if x]
		macro = ml[0]

		# Is this a valid macro?
		mf = macros.get_macro(macro)
		if not mf:
			return False

		# Some macros want to treat trailing text literally,
		# not try to stitch together arguments. They give us
		# an explicit argument count, and the last argument
		# is it.
		acount = macros.arg_count(macro)
		if acount and len(mla) > acount:
			res = mac.split(':', acount)
			#rest = res[-1]
			#mla = mla[0:acount-1] + [rest,]
			mla = res[1:]

		# Is this macro callable with our current options?
		# We use this to fastpath checks for restrictions and
		# other things.
		# If macros are off, {{...}} must render
		# as literal text.
		# The exception is the 'this isn't really a macro,
		# it's markup' macros. This is considered safe, even
		# for C, because people could just put in the raw UTF-8
		# bytes anyways.
		if macros.text_macro(macro):
			pass
		elif self.options & NOMACROS:
			return False
		# Disallowed macros are just swallowed.
		elif not self.allowedmacro(macro):
			# We can't bold this or anything, because
			# we don't know (without looking carefully)
			# what tags we need to generate to do that.
			if self.options & SOMEMACROS:
				self.result.append("{macro suppressed}")
			return True

		# Okay, call it and see if it is happy with life.
		# Otherwise, bye bye!
		if mf(self, mla):
			return True
		else:
			return False

	# ....
	# It is a code smell that this is duplicated between here and
	# inline_font.
	def start_style(self, style):
		self.inlineEndStack.insert(0, end_entity[style])
		self.result.append(start_entity[style])
	def end_style(self, style):
		offtag = end_entity[style]
		s = self.inlineEndStack.pop(0)
		while s != offtag:
			self.result.append(s)
			s = self.inlineEndStack.pop(0)
		# s == offtag.
		self.result.append(offtag)

	# Generate a list, either real or striped.
	# If we are generating a real list inside a paragraph, HTML
	# propriety requires that we kill the paragraph and then
	# resume it afterwards, as <ul> doesn't nest inside <p>.
	def macro_list(self, lifunc, bfunc, elems):
		if self.useLists:
			in_para = False
			if self.blockEndStack[0] == end_entity['p']:
				in_para = True
				self.handle_end('end', 'p')
			self.handle_begin("begin", 'ul')
			for e in elems:
				self.handle_begin("begin", 'li')
				lifunc(e)
				self.handle_end("end", 'li')
			self.handle_end("end", 'ul')
			if in_para:
				self.handle_begin('begin', 'p')
		else:
			# The standard striped form is a comma
			# separated result, which we generate here
			# so that the rendering functions are simpler.
			first = True
			for e in elems:
				if not first:
					self.result.append(", ")
				bfunc(e)
				first = False

	# Public interface that macros use
	# Backwards compatibility stuff only
	def text(self, txt, renderstyle = "full"):
		if renderstyle == "full":
			self.handle_text("text", txt)
		elif renderstyle == "fonts":
			self.handle_text("ltext", txt)
		else:
			self.handle_plaintext(txt)
	def addPiece(self, txt):
		self.result.append(txt)
	def makelink(self, target, name):
		linkend = self.makelinkstart(target)
		self.handle_plaintext(name)
		if linkend:
			self.result.append('</a>')
	def addFeature(self, feature):
		self.features.append(feature)
	def closeRendering(self):
		"Only for CutShort"
		self.result.extend(self.inlineEndStack)
		self.result.extend(self.blockEndStack)
		self.inlineEndStack = []
		self.blockEndStack = []
	def markComplex(self):
		self.hasComplex = True

	def disableStyle(self, special):
		self.disabledStyles[special] = True
	def enableStyles(self):
		self.disabledStyles = {}
	def makeSub(self, word, newword, wordize = False):
		# This is straight text substitution and goes off ANYWHERE,
		# because that's the only honest way. We could try using
		# '[\b\W]', but that's only partially right; we cannot do
		# what we really want to do, which is not do this substution
		# on non-text embedded in text, such as URLs in [[...]].
		if wordize:
			src = r'(?<=[\b\W])%s(?=[\b\W])' % re.escape(word)
		else:
			src = r"%s" % re.escape(word)
		sre = re.compile(src)
		newword = newword.replace("\\", r"\\")
		self.textSubs[word] = (sre, newword)
	def delSub(self, word):
		if word in self.textSubs:
			del self.textSubs[word]
	def delAllSubs(self):
		self.textSubs = {}

	# For macros: insert a link to a page. The title of the link is
	# either pn or the title of the page, depending on the setting
	# of usePageTitles. Because titles come back as HTML, we must
	# do a manual dance to insert them.
	def link_to_page(self, path, pn, view):
		purl = self.web.url_from_path(path, view)
		# The non page title
		if not self.usePageTitles:
			self.makelink(purl, pn)
			return		

		# Getting the renderer this way gets us the version with
		# caching built in.
		rfunc = htmlrends.get_renderer("wikitext:title:nolinks")
		np = self.ctx.model.get_page(path)
		# We don't exclude np.is_util() pages from getting titles
		# because we have them in the list anyways, so we might as
		# well show their titles.
		if np.type != "file" or not np.realpage() or \
		   not np.access_ok(self.ctx):
			self.makelink(purl, pn)
			return
		
		nc = self.ctx.clone_to_page(np)
		ltitle = rfunc(nc)
		if not ltitle:
			self.makelink(purl, pn)
			return

		self.ctx.newtime(nc.modtime)
		linkend = self.makelinkstart(purl)
		self.result.append(ltitle)
		if linkend:
			self.result.append("</a>")

#
# ----
# Caching support.
#
# This must go here, because we use it when we register stuff.
def cacheWrap(func, name):
	def _cachewrapped(ctx):
		# Are we running caching at all?
		if not rendcache.cache_on(ctx.cfg):
			return func(ctx)
		
		# Fetch automatically validates.
		res = rendcache.fetch(ctx, name)

		# Cache hit; crack the results and generate.
		if res:
			(rend, ftrs, auth, title) = res
			for f in ftrs:
				ctx.addfeature(f)
			if auth is not None:
				set_cache_features(auth, ctx.page, ctx)
			if title is not None:
				ctx.setvar(":wikitext:title", title)
			ctx.newtime(ctx.page.modstamp)
			return rend

		# The rendering of a page may be non-cacheable because
		# of complex macros.
		rend = func(ctx)
		if ctx.hasfeature('indirect-macros'):
			return rend
		ftrs = ctx.getfeatures()
		auth = get_cache_features(ctx.page, ctx)
		title = ctx.get(":wikitext:title", None)
		stuple = (rend, ftrs, auth, title)

		# Now we need the validator. For a non-fancy page, all
		# outside things that can affect it are WikiWords, which
		# only change if the current directory, the root directory,
		# or the aliases directory change.
		v = rendcache.Validator()
		v.add_ctime(ctx.page)
		v.add_mtime(ctx.page.curdir())
		v.add_mtime(ctx.model.get_page(''))
		# Add the search path directories, if any, to the validator.
		for sd in ctx.get(':wikitext:search', []):
			# It is possible to typo a search directory, so
			# that it doesn't exist. Adding these pages to the
			# cache validator explodes.
			spg = ctx.model.get_page(sd)
			if spg and spg.exists():
				v.add_mtime(spg)
		if 'alias-path' in ctx.cfg:
			v.add_mtime(ctx.model.get_page(ctx.cfg['alias-path']))
		# tricky case: we must make this user-dependant if there is
		# an access restriction anywhere up the tree (.access_on()),
		# not just if this page has a direct access restriction.
		if ftrs is not None and \
		   not ('hascomments' in ftrs or 'hasrestricted' in ftrs) and \
		   not ctx.page.access_on(ctx):
			perUser = False
		else:
			perUser = True
		rendcache.store(ctx, name, stuple, v, perUser = perUser)
		return rend

	# We must copy __doc__ to have the renderer documentation come
	# out right.
	_cachewrapped.__doc__ = func.__doc__
	return _cachewrapped

def registerCached(name, func):
	htmlrends.register(name, cacheWrap(func, name))
#
# ------------

#
# This is the public rendering interface for rendering generic content.
def wikirend(data, ctx, options = None):
	r = WikiRend(data, ctx, options)
	return r.render()

# Internal invocation with magic cache and options support.
# This is the real backend of various wikitext renderers.
def _render(ctx, options):
	if ctx.page.type != "file":
		raise derrors.RendErr, "wikitext asked to render non-file."
	if not ctx.page.displayable():
		raise derrors.RendErr, "wikitext asked to render undisplayable page"

	# *NOTE*: our callers never block Restricted/CanComment.
	ftrable = (options & set_features_option)
	
	ctx.newtime(ctx.page.modstamp)
	# A page with no access restrictions of its own might be under
	# access restrictions from a parent directory; if we rendered
	# it without checking, we would return good results when we
	# shouldn't.
	# It is worth optimizing this check, because checking permissions
	# requires some sort of page rendering; we do not want to render
	# twice.
	data = ctx.page.contents()
	res = None
	if not no_restricted(data):
		res = wikirend(data, ctx, options)
		ftrs = ctx.getfeatures()
		# If we did not actually declare a restriction,
		# and we are restricted by our parents, null the
		# rendering.
		if 'hasrestricted' not in ftrs and \
		   not ctx.page.render_ok(ctx):
			res = ''
	elif ctx.page.render_ok(ctx):
		res = wikirend(data, ctx, options)
		ftrs = ctx.getfeatures()
	else:
		res = ''
		ftrable = False

	if ftrable:
		set_cache_features(ftrs, ctx.page, ctx)
	return res

# This is the renderer interface; it renders the contents of the current
# page (file), and as a bonus sets the context timestamp correctly so
# you don't have to.
def render(ctx):
	"""Convert wikitext into HTML."""
	return _render(ctx, rendering_flags)
# Some people call .render directly, so we wrap the function itself
# and then register normally.
render = cacheWrap(render, 'wikitext')
htmlrends.register('wikitext', render)

# Authentication in the face of cutting things short is one of those
# thorny issues that we decide to declare a feature. With cut-short
# rendering, only authentication up to that point is checked. This
# means you can have a short teaser and then a larger authenticated
# payload.
def shortrend(ctx):
	"""Convert wikitext into HTML, honoring the !{{CutShort}} macro."""
	return _render(ctx, ALLOW_CUTSHORT | set_title_option | set_features_option)
#htmlrends.register("wikitext:short", shortrend)
registerCached("wikitext:short", shortrend)

def terserend(ctx):
	"""Convert wikitext into terse 'absolute' HTML, with all links
	fully qualified and no macros having any effect except CutShort,
	CanComment, IMG, and Restricted."""
	return _render(ctx, terse_flags | ABSLINKS | set_title_option)
#htmlrends.register("wikitext:terse", terserend)
registerCached("wikitext:terse", terserend)

# This one is a bit peculiar, since we don't actually want the
# rendering output, just one of the side effects. (Note that
# it is actually faster to always render than to check for
# alternate forms that might already be cached. Doing just one
# line of wikitext is fast.)
# We must also check access permissions explicitly; because we
# chop rendering short, we may not activate embedded restrictions.
def gen_title(ctx, eflags = 0):
	if ctx.page.type != "file" or not ctx.page.access_ok(ctx):
		return ''
	_render(ctx, set_title_option | TITLEONLY | eflags)
	return ctx.get(':wikitext:title', '')
	
def titlerend(ctx):
	"""Generate and return the title of a wikitext page."""
	return gen_title(ctx, 0)
registerCached("wikitext:title", titlerend)

# This uses the simple cache registration, because without links it's
# not dependant on what links resolve to, so it is not dependant on
# anything except the page itself.
def nolinkstitlerend(ctx):
	"""Generate and return the title of a wikitext page without links."""
	return gen_title(ctx, NOLINKS)
rendcache.registerSimpleCached("wikitext:title:nolinks", nolinkstitlerend)

# Return the title with all HTML stripped off. This is easier than it
# looks because we know we are well formed so we can just remove all
# elements with a simple regexp. (We render NOLINKS as well to remove
# the amount of work we need to do; better to not generate at all than
# generate and remove.)
#
# The tradeoff between generating titles separately and rendering the
# full page is perhaps obscure. In the usual contexts for this (eg,
# HTML page titles) we are almost certainly going to go ahead and
# render the full page later. For now I am opting to keep this simple
# and straightforward.
striphtml_re = re.compile("<[^>]+>")
def nohtmltitlerend(ctx):
	"""Generate and return the title of a wikitext page without HTML
	markup."""
	return striphtml_re.sub("", gen_title(ctx, NOLINKS))
rendcache.registerSimpleCached("wikitext:title:nohtml", nohtmltitlerend)

#
# Checks to see if we definitely cannot have restrictions or feature
# settings in the data. Even if these return false we may still not,
# but if they return true they're guaranteed to be right.
canc_re = re.compile("\{\{CanComment")
rest_re = re.compile("\{\{Restricted")
def no_restricted(data):
	return not (rest_re.search(data) and not is_plaintext(data))
def no_featmacros(data):
	return not ((rest_re.search(data) or canc_re.search(data)) and \
		    not is_plaintext(data))

# We misuse the authent cache to cache page features. That's OK;
# page features more or less *are* the authentication system, so.
def set_cache_features(ftrs, page, ctx):
	ctx.setauthent("features", page.path, ftrs)
def get_cache_features(page, ctx):
	return ctx.getauthent("features", page.path)

# Generate a list of the page features, via a restricted rendering.
# Page features are cached (and returned if available).
def gen_page_features(page, ctx):
	res = get_cache_features(page, ctx)
	if res is not None:
		return res

	data = page.contents()
	# No apparent macros, or plaintext, means that we can
	# immediately punt.
	if no_featmacros(data):
		res = []
	else:
		# We must generate the page features through a rendering
		# pass.
		ctx2 = ctx
		if ctx2.page != page:
			# We must make a new context so that our features
			# don't mingle with the normal page's features.
			ctx2 = ctx2.clone_to_page(page)
		wikirend(data, ctx2, check_flags)
		res = ctx2.getfeatures()

	set_cache_features(res, page, ctx)
	return res