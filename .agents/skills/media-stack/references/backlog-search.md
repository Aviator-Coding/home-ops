# Missing-movie backlog search

Radarr has no scheduled missing-movie task. Every import list on this stack
runs `searchOnAdd: false`. A movie a list adds stays `monitored: true` with no
file until a new RSS release matches, or a person runs a search. RSS
`Reports grabbed: 0` looks the same when the library is complete and when
hundreds of movies were never searched.

Run a search after a `min_format_score` change, a large list add, or a long
Radarr outage. Size it against free disk first. A few hundred movies is
multiple terabytes, and nothing else in the stack checks.

1. `GET /api/v3/movie`. Count `monitored && !hasFile` per `qualityProfileId`.
2. For a small sample per profile, `GET /api/v3/release?movieId=<id>` and read
   the accepted releases' sizes and hit rate. Documentary and TV-movie titles
   often have no HD-Bluray-tier release. Do not use one average for every
   profile.
3. hit-rate times size times population, summed, against
   `GET /api/v3/rootfolder` free space. If the estimate is more than about
   half of free space, stage a subset or stop for a decision.
4. Search in batches: `POST /api/v3/command` with
   `{"name":"MoviesSearch","movieIds":[...]}`, about 20 ids, and a pause on
   the order of two minutes. Indexers are proxied through Prowlarr and several
   have daily quotas. One unpaced burst can get the indexer set banned.
5. Confirm grabs in `GET /api/v3/queue` and that the client is actually
   downloading. Movies that still match nothing are an outcome, not a failed
   command. Group them (language, no release group, not released) before
   changing the profile again.

`searchOnAdd: true` on the import lists would search a newly added movie
immediately. It is not turned on. It changes every future list add and needs
the same quota check. Recyclarr does not manage those lists.

Cutoff-unmet upgrades are a different action: Radarr Movies, Cutoff Unmet,
Search. That queue is movies that already have a file.
