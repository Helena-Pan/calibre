#!/usr/bin/env python2
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:fdm=marker:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__   = 'GPL v3'
__copyright__ = '2013, Kovid Goyal <kovid at kovidgoyal.net>'
__docformat__ = 'restructuredtext en'

import copy
from functools import partial
from future_builtins import map

from calibre.ebooks.metadata import author_to_author_sort
from calibre.utils.config_base import tweaks
from calibre.utils.icu import sort_key, collation_order

CATEGORY_SORTS = ('name', 'popularity', 'rating')  # This has to be a tuple not a set

class Tag(object):

    __slots__ = ('name', 'original_name', 'id', 'count', 'state', 'is_hierarchical',
            'is_editable', 'is_searchable', 'id_set', 'avg_rating', 'sort',
            'use_sort_as_name', 'category', 'search_expression')

    def __init__(self, name, id=None, count=0, state=0, avg=0, sort=None,
                 category=None, id_set=None, search_expression=None,
                 is_editable=True, is_searchable=True, use_sort_as_name=False):
        self.name = self.original_name = name
        self.id = id
        self.count = count
        self.state = state
        self.is_hierarchical = ''
        self.is_editable = is_editable
        self.is_searchable = is_searchable
        self.id_set = id_set if id_set is not None else set()
        self.avg_rating = avg/2.0 if avg is not None else 0
        self.sort = sort
        self.use_sort_as_name = use_sort_as_name
        self.category = category
        self.search_expression = search_expression

    def __unicode__(self):
        return u'%s:%s:%s:%s:%s'%(self.name, self.count, self.id, self.state, self.category)

    def __str__(self):
        return unicode(self).encode('utf-8')

    def __repr__(self):
        return str(self)

def find_categories(field_metadata):
    for category, cat in field_metadata.iteritems():
        if (cat['is_category'] and cat['kind'] not in {'user', 'search'}):
            yield (category, cat['is_multiple'].get('cache_to_list', None), False)
        elif (cat['datatype'] == 'composite' and
              cat['display'].get('make_category', False)):
            yield (category, cat['is_multiple'].get('cache_to_list', None), True)

def create_tag_class(category, fm):
    cat = fm[category]
    dt = cat['datatype']
    is_editable = category not in {'news', 'rating', 'languages', 'formats',
                                   'identifiers'} and dt != 'composite'

    if (tweaks['categories_use_field_for_author_name'] == 'author_sort' and
            (category == 'authors' or
                (cat['display'].get('is_names', False) and
                cat['is_custom'] and cat['is_multiple'] and
                dt == 'text'))):
        use_sort_as_name = True
    else:
        use_sort_as_name = False

    return partial(Tag, use_sort_as_name=use_sort_as_name,
                   is_editable=is_editable, category=category)

def clean_user_categories(dbcache):
    user_cats = dbcache.pref('user_categories', {})
    new_cats = {}
    for k in user_cats:
        comps = [c.strip() for c in k.split('.') if c.strip()]
        if len(comps) == 0:
            i = 1
            while True:
                if unicode(i) not in user_cats:
                    new_cats[unicode(i)] = user_cats[k]
                    break
                i += 1
        else:
            new_cats['.'.join(comps)] = user_cats[k]
    try:
        if new_cats != user_cats:
            dbcache.set_pref('user_categories', new_cats)
    except:
        pass
    return new_cats

def sort_categories(items, sort, first_letter_sort=False):
    if sort == 'popularity':
        key=lambda x:(-getattr(x, 'count', 0), sort_key(x.sort or x.name))
    elif sort == 'rating':
        key=lambda x:(-getattr(x, 'avg_rating', 0.0), sort_key(x.sort or x.name))
    else:
        if first_letter_sort:
            key=lambda x:(collation_order(icu_upper(x.sort or x.name or ' ')),
                          sort_key(x.sort or x.name))
        else:
            key=lambda x:sort_key(x.sort or x.name)
    items.sort(key=key)
    return items

def get_categories(dbcache, sort='name', book_ids=None, first_letter_sort=False):
    if sort not in CATEGORY_SORTS:
        raise ValueError('sort ' + sort + ' not a valid value')

    fm = dbcache.field_metadata
    book_rating_map = dbcache.fields['rating'].book_value_map
    lang_map = dbcache.fields['languages'].book_value_map

    categories = {}
    book_ids = frozenset(book_ids) if book_ids else book_ids
    pm_cache = {}

    def get_metadata(book_id):
        ans = pm_cache.get(book_id)
        if ans is None:
            ans = pm_cache[book_id] = dbcache._get_proxy_metadata(book_id)
        return ans

    bids = None

    for category, is_multiple, is_composite in find_categories(fm):
        tag_class = create_tag_class(category, fm)
        if is_composite:
            if bids is None:
                bids = dbcache._all_book_ids() if book_ids is None else book_ids
            cats = dbcache.fields[category].get_composite_categories(
                tag_class, book_rating_map, bids, is_multiple, get_metadata)
        elif category == 'news':
            cats = dbcache.fields['tags'].get_news_category(tag_class, book_ids)
        else:
            cat = fm[category]
            brm = book_rating_map
            if cat['datatype'] == 'rating' and category != 'rating':
                brm = dbcache.fields[category].book_value_map
            cats = dbcache.fields[category].get_categories(
                tag_class, brm, lang_map, book_ids)
            if (category != 'authors' and cat['datatype'] == 'text' and
                cat['is_multiple'] and cat['display'].get('is_names', False)):
                for item in cats:
                    item.sort = author_to_author_sort(item.sort)
        sort_categories(cats, sort, first_letter_sort=first_letter_sort)
        categories[category] = cats

    # Needed for legacy databases that have multiple ratings that
    # map to n stars
    for r in categories['rating']:
        for x in tuple(categories['rating']):
            if r.name == x.name and r.id != x.id:
                r.id_set |= x.id_set
                r.count = r.count + x.count
                categories['rating'].remove(x)
                break

    # User categories
    user_categories = clean_user_categories(dbcache).copy()

    # First add any grouped search terms to the user categories
    muc = dbcache.pref('grouped_search_make_user_categories', [])
    gst = dbcache.pref('grouped_search_terms', {})
    for c in gst:
        if c not in muc:
            continue
        user_categories[c] = []
        for sc in gst[c]:
            for t in categories.get(sc, ()):
                user_categories[c].append([t.name, sc, 0])

    if user_categories:
        # We want to use same node in the user category as in the source
        # category. To do that, we need to find the original Tag node. There is
        # a time/space tradeoff here. By converting the tags into a map, we can
        # do the verification in the category loop much faster, at the cost of
        # temporarily duplicating the categories lists.
        taglist = {}
        for c, items in categories.iteritems():
            taglist[c] = dict(map(lambda t:(icu_lower(t.name), t), items))

        # Add the category values to the user categories
        for user_cat in sorted(user_categories.iterkeys(), key=sort_key):
            items = []
            names_seen = {}
            user_cat_is_gst = user_cat in gst
            for name, label, ign in user_categories[user_cat]:
                n = icu_lower(name)
                if label in taglist and n in taglist[label]:
                    if user_cat_is_gst:
                        # for gst items, make copy and consolidate the tags by name.
                        if n in names_seen:
                            t = names_seen[n]
                            other_tag = taglist[label][n]
                            t.id_set |= other_tag.id_set
                            t.count += other_tag.count
                        else:
                            t = copy.copy(taglist[label][n])
                            names_seen[n] = t
                            items.append(t)
                    else:
                        items.append(taglist[label][n])
                # else: do nothing, to not include nodes w zero counts
            cat_name = '@' + user_cat  # add the '@' to avoid name collision
            categories[cat_name] = sort_categories(items, sort)

    # ### Finally, the saved searches category ####
    items = []
    queries = dbcache._search_api.saved_searches.queries
    for srch in sorted(queries, key=sort_key):
        items.append(Tag(srch, sort=srch, search_expression=queries[srch],
                         category='search', is_editable=False))
    if len(items):
        categories['search'] = items

    return categories
