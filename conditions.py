#
# Conditional renderers.
# This is an evil way to introduce ifs into the whole process, but that's
# the way I'm going.
import htmlrends
import pageranges

def notblogroot(context):
	"""Succeds (by generating a space) if we are a directory that
	is in a default blog view but is not the directory that made
	it the default view. Fails otherwise."""
	if context.page.type != "dir" or context.view != "blog":
		return ''
	(pv, vdir) = context.pref_view_and_dir(context.page)
	if vdir == context.page:
		return ''
	return ' '
htmlrends.register("cond::notblogroot", notblogroot)

def isblogyearmonth(context):
	"""Suceeds (by generating a space) if we are a directory, in a
	blog view, and we are in a month or year VirtualDirectory.
	Fails otherwise."""
	if context.view != "blog" or \
	   not pageranges.is_restriction(context) or \
	   pageranges.restriction(context) not in ('year', 'month'):
		return ''
	else:
		return ' '
htmlrends.register("cond::blogyearmonth", isblogyearmonth)

def isanon(context):
	"""Suceeds (by generating a space) if this is an anonymous
	request, one with no logged in real user. Fails otherwise."""
	if context.current_user() and not context.is_login_default():
		return ''
	else:
		return ' '
htmlrends.register("cond::anonymous", isanon)

def isrealuser(context):
	"""Suceeds (by generating a space) if this is a request made
	by a logged-in real user. Fails otherwise. This is the opposite
	of _cond::anonymous_."""
	if context.current_user() and not context.is_login_default():
		return ' '
	else:
		return ''
htmlrends.register("cond::realuser", isrealuser)
