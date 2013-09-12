#
# This module supplies an interface to read from files with
# RFC822 style 'continued lines', with comments and blank lines
# excised.
import re

class StartingContinuedLine(Exception):
	pass

# This skips lines that are entirely blank up to a comment start
# character.
skipre = re.compile('^\s*(#|$)')

class RFC822File(object):
	def __init__(self, fp):
		self._f = fp
		self.closed = 0
		# Lineno is the line number of the start of the possibly
		# continued line. curln is the current true line number.
		# accum is the accumulated line to date.
		self._lineno = 0
		self._curln = 0
		self._accum = ''
	def close(self):
		self._f.close()
		self._f = None
		self.closed = 1

	def _getline(self):
		while True:
			l = self._f.readline()
			# Bail on EOF:
			if not l:
				break
			# We must count even blanks and comments.
			self._curln += 1

			# Skip blanks and comments.
			if skipre.match(l):
				continue

			# Is this a continued line, or not?
			if self._accum and l[0] in ' \t':
				self._accum = self._accum.rstrip() + \
					      ' ' + l.lstrip()
			elif l[0] in ' \t':
				raise StartingContinuedLine, "The first real line, at line number %d, is a continuation." % (self._curln,)
			else:
				res = None
				# If accum is non-null, we have just finished
				# off another (possibly continued) line and
				# want to return it. Otherwise, this is the
				# first real line in the file.
				if self._accum:
					res = (self._lineno, self._accum)
				self._lineno = self._curln
				self._accum = l
				if res:
					return res
		# We have seen an EOF. Return anything accumulated,
		# and set it so that we will continue to return EOF.
		res = (self._lineno, self._accum)
		self._accum = ''
		return res
		
	def readcontline(self):
		return self._getline()[1]
	def readcontline_ex(self):
		res = self._getline()
		if res[1] == '':
			return ''
		else:
			return res

	def __iter__(self):
		while True:
			l = self.readcontline()
			if not l:
				break
			yield l

def fromfile(fp):
	return RFC822File(fp)
# Note that only 'r' based modes make any sense: r, rb, U, Ur.
# Generally, don't do that.
def openfile(filename, mode = 'r'):
	return RFC822File(open(filename, mode))
