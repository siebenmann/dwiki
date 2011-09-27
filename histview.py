#
# HTML view renders for components of a file's history, provided that the
# file actually has history.
import time, calendar

import htmlrends

def showactive(context):
	"""If the current page is under RCS and is locked, display who has
	locked it."""
	if not context.page.hashistory():
		return ''
	res = context.page.current_user()
	if not res:
		return ''
	return res
htmlrends.register("hist::lockedby", showactive)

# TODO: somehow, show diffs/etc.
def cell(str):
	return "<td>%s</td>" % str
def showrevs(context):
	"""If the current page is under RCS, display a version history
	table."""
	if not context.page.hashistory():
		return ''
	hl = context.page.history()
	if not hl:
		return ''

	# We format this into a table for now, because I feel like it.
	result = []
	result.append("<table border=1>")
	result.append("<caption>Page revision history</caption>\n")
	result.append("<tr><th>At</th><th>Made by</th><th>Revision</th></tr>")
	for he in hl:
		dl = [int(x) for x in he[2].split('.')]
		dl.extend((0, 0, 0))
		secs = calendar.timegm(dl)
		#dstr = time.strftime("%c %Z", time.localtime(secs))
		dstr = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(secs))
		result.append("\n<tr> %s %s %s </tr>" % (cell(dstr),
							 cell(he[1]),
							 cell(he[0])))
	result.append("\n</table>\n")
	return ''.join(result)
htmlrends.register("hist::revtable", showrevs)

def isdirty(context):
	"""If the current page has been RCS-locked, display whether or not
	it has been modified from the version in RCS."""
	if not context.page.hashistory():
		return ''
	res = context.page.current_user()
	if not res:
		return ''
	st = context.page.isdirty()
	if st:
		return "The page has been modified."
	else:
		return "The page has not yet been modified."
htmlrends.register("hist::dirty", isdirty)
