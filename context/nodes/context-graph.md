# Context Graph

Ciel uses a graph to keep future AI context large without making every prompt large.

The source of truth is `context/graph.json` plus focused Markdown nodes. Runtime retrieval compiles these files into a disposable SQLite cache.

Retrieval flow:

1. lexical search selects a small seed set
2. graph expansion follows at most a bounded number of hops
3. results are ranked and limited
4. only the selected context is supplied to the model

The SQLite schema uses namespaces so project architecture, user memory and future character knowledge can coexist without id collisions.

SQLite FTS5 is used when available. A LIKE fallback keeps the system functional on Python builds without FTS5.

Future embedding search should be added as another candidate generator. It must not replace graph relationships or the Markdown source files.
