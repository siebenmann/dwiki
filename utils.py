#
import re

# The less helpful version of os.path.join, for two arguments only, and
# if the second argument is an absolute path we do not discard the first.
# Sort of. This one is explicitly for URLs only, so we always use '/'
# as the path separator.
def pjoin(a, b):
	if not a: return b
	if not b: return a
	return a + '/' + b

# Canonicalize a path into a normalized form, returning the normalized
# path and the the 'name', the last component.
# Paths returned are absolute paths but do not start with a '/';
# the root is ''.
def canon_path(path):
	path = path.strip('/')
	if not path:
		return ('', '')
	name = path.split('/')[-1]
	return (name, path)

# The parent is the path minus the name, and necessarily '' at the root.
def parent_path(path):
	ps = path.split('/')
	if len(ps) == 1:
		return ''
	else:
		return '/'.join(ps[:-1])
def name_path(path):
	if '/' not in path:
		return path
	return path.split('/')[-1]

# Like os.walk, but on pages and does not return directories.
# TODO: unused and worth removing?
def walk(page):
	res = []
	for child in page.children():
		if child.type != "dir":
			res.append(child)
		else:
			res.extend(walk(child))
	return res

# We are in dirn, with (relative) path path. Canonicalize the
# result in the presence of '..' and other fun and games.
# If the result escapes dirn, return None.
def canonpath(dirn, path):
	# First, take out all '/./' cases in the path.
	sl = [x for x in path.split('/') if x != '.']
	dl = dirn.split('/')
	# We insert a fake root, because all of our canonical paths
	# don't start with a '/'.
	dl.insert(0, '')
	while dl and sl:
		top = sl.pop(0)
		if top == '..':
			dl.pop()
		else:
			dl.append(top)
	# We have path elements left over due to '..' running off the
	# top.
	if sl:
		return None
	dl.pop(0)
	return '/'.join(dl)

# Is a path a good path?
# This is called SO OFTEN that it is worth some micro optimizations.
# Note: good_path_elem() beats an RE-based matcher.
badElem = dict.fromkeys(('.', '..', '', 'RCS'))
def good_path_elem(pelem):
	return not (pelem in badElem or \
		    pelem[0] == '.' or \
		    pelem[-1] == '~' or \
		    pelem[-2:] == ",v")
def goodpath_old(path):
	if path == '':
		return True
	pelems = [x for x in path.split('/') if not good_path_elem(x)]
	return not bool(pelems)

# note that the '.'-at-start pattern takes out '..' as well.
# a path that starts with a / is not good, but this is tricky
# in the re; it matches '^<empty>/'.
# This beats goodpath_old().
badpath_re = re.compile(r"(^|/)(\.[^/]*|RCS||[^/]*~|[^/]*,v)(/|$)")
def goodpath(path):
	if badpath_re.search(path) and path != "":
		return False
	else:
		return True

# A bogus path is one that has directory motion elements in it that
# make us grind our teeth.
# Note that split's behavior means that disallowing '' as a path
# element also disallows paths starting with '/'.
# As surprising as it might be, this implementation beats a regexp.
def boguspath(path):
	if path == '':
		return False
	pelems = [x for x in path.split('/') if x in ('.', '..', '')]
	return bool(pelems)

#
def yield_names(plist):
	for path in plist:
		if not path:
			yield path
		else:
			yield path.split("/")[-1]

# Walk up to the root, yielding everything going.
def walk_to_root(page):
	while page.path != '':
		yield page
		page = page.parent()
	yield page
	# and we're done

# Return the common prefix of paths a and b.
def common_prefix(a, b):
	l1 = a.split('/')
	l2 = b.split('/')
	i = 0
	while i < len(l1) and i < len(l2):
		if l1[i] != l2[i]:
			break
		i += 1
	return '/'.join(l1[:i])

# This is a stable sort for 'time lists', as returned from eg
# page_children(). It sorts most recent first and breaks ties by
# sorting on the page path.
def timelist_sorter(a, b):
	return cmp(b[0], a[0]) or cmp(a[1], b[1])

# Sort a timelist like thing into what we consider order.
# There are two approaches to this: .sort(timelist_sorter) or
# just .sort() + .reverse. The latter is much faster, but
# reverses the alphabetical order for things with the same
# timestamp (you get (10, "ghi"), (10, "def"), (10, "abc")
# instead of vice versa). Arguably this is the right thing,
# and it certainly is faster, and identical timestamps are
# unlikely anyways.
def sort_timelist(lst):
	lst.sort(); lst.reverse()
	# or:
	#lst.sort(timelist_sorter)
