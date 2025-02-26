from abc import ABC, abstractmethod
from collections import OrderedDict
import json

from .inverted_index import Index

def extractor(node):
    text = []
    if isinstance(node, Literal):
        text.append(node.text)
        if node.aliases:
            text.append(node.aliases)
    return text
    
class TypeIndex:
    def __init__(self):
        self._index = Index(extractor)
        pass

    def add(self, node, terms):
        self._index.add(node)

    def nodes(self, terms):
        matches = self._index.match(terms)
        return matches


class SymbolTable:
    def __init__(self):
        self.nodes = {}

    def add(self, key, type):
        if key in self.nodes:
            raise ValueError(f"Key {key} already exists in the graph.")
        self.nodes[key] = type

    def get(self, key):
        value = self.nodes.get(key)
        if value is None:
            raise KeyError(f"Key {key} not found in the graph.")
        return value

    def print(self):
        for key, type in self.nodes.items():
            print(f"{key}: {type.format()}")


class Subgraph:
    def __init__(self, symbols, nodes):
        self._symbols = symbols
        self._nodes = set(nodes)
        self._filtered = {}
        self._context = []

    def keep(self, node):
        return node in self._nodes
    # is_local(), push(), and pop() are for handling type parameters.
    def is_local(self, key):
        for symbols in self._context:
            if key in symbols:
                return True
        return False

    def original(self, key):
        return self._symbols.get(key)

    def filtered(self, key):
        return self._filtered.get(key)

    def add(self, key, type):
        if key in self._filtered:
            raise ValueError(f"Key {key} already exists in the graph.")
        self._filtered[key] = type

    def push(self, symbols):
        self._context.append(symbols)

    def pop(self):
        self._context.pop()


class Node(ABC):
    next_id = 0

    def __init__(self):
        self.id = Node.next_id
        Node.next_id += 1

    @abstractmethod
    def format(self):
        pass

    @abstractmethod
    def index(self, symbols, indexer):
        pass

    @abstractmethod
    def filter(self, nodes):
        pass

    @abstractmethod
    def visit(self, subgraph, visitor):
        pass


class Define(Node):
    def __init__(self, name, params, type):
        self.name = name
        self.params = params
        self.type = type

    def format(self):
        params = (
            f"<{",".join([p.format() for p in self.params])}>"
            if len(self.params or []) > 0
            else ""
        )
        return f"type {self.name}{params}={self.type.format()};"

    def index(self, symbols, indexer):
        for param in self.params:
            param.index(symbols, indexer)
        self.type.index(symbols, indexer)

    def filter(self, subgraph):
        print(f"FILTER: {self.format()}")
        # TODO: do we filter type parameters?
        filtered_params = [p.filter(subgraph) for p in self.params]
        if any(isinstance(p, Never) for p in filtered_params):
            return Never()
        # for p in self.params:
        #     p.filter(subgraph)
        context = [p.name for p in self.params]
        if len(context) > 0:
            subgraph.push(context)
        t = self.type.filter(subgraph)
        if len(context) > 0:
            subgraph.pop()
        return Define(self.name, filtered_params, t)
    
    def visit(self, subgraph, visitor):
        # print(f"visit: {self.format()}")
        visitor(self)
        for p in self.params:
            p.visit(subgraph, visitor)
        self.type.visit(subgraph, visitor)


class Never(Node):
    def __init__(self):
        pass

    def format(self):
        return "never"

    def index(self, symbols, indexer):
        pass

    def filter(self, nodes):
        return self
    
    def visit(self, subgraph, visitor):
        visitor(self)
        pass


class Param(Node):
    def __init__(self, name, extends=None):
        self.name = name
        self.extends = extends

    def format(self):
        return self.name + (f" extends {self.extends.format()}" if self.extends else "")

    def index(self, symbols, indexer):
        if self.extends:
            self.extends.index(symbols, indexer)

    # TODO: do we filter extends logic?
    def filter(self, nodes):
        if self.extends:
            t = self.extends.filter(nodes)
            if isinstance(t, Never):
                return Never()
            return Param(self.name, t)
        return self
    
    def visit(self, subgraph, visitor):
        visitor(self)
        if self.extends:
            self.extends.visit(subgraph, visitor)


class Union(Node):
    def __init__(self, *types):
        self.types = types

    def format(self):
        return "|".join([t.format() for t in self.types])

    def index(self, symbols, indexer):
        for type in self.types:
            type.index(symbols, indexer)

    def filter(self, nodes):
        types = [t.filter(nodes) for t in self.types]
        filtered = [t for t in types if not isinstance(t, Never)]
        if len(filtered) > 0:
            return Union(*filtered)
        return Never()
    
    def visit(self, subgraph, visitor):
        visitor(self)
        for t in self.types:
            t.visit(subgraph, visitor)


class Literal(Node):
    def __init__(self, text, aliases=None):
        self.text = text
        self.aliases = aliases

    def format(self):
        return json.dumps(self.text)

    def index(self, symbols, indexer):
        indexer.add(self, self.text)
        if self.aliases:
            for alias in self.aliases:
                indexer(self, alias)

    def filter(self, subgraph):
        return self if subgraph.keep(self) else Never()
    
    def visit(self, subgraph, visitor):
        visitor(self)


class Struct(Node):
    def __init__(self, obj):
        self.obj = obj

    def format(self):
        return "{" + ",".join(f'"{k}":{v.format()}' for k, v in self.obj.items()) + "}"

    def index(self, symbols, indexer):
        for k, v in self.obj.items():
            v.index(symbols, indexer)

    def filter(self, subgraph):
        obj = {k: v.filter(subgraph) for k, v in self.obj.items()}
        filtered = {k: v for k, v in obj.items() if not isinstance(v, Never)}
        return Struct(filtered) if len(filtered) == len(obj) else Never()
        # return Struct(filtered) if len(filtered) > 0 else Never()
    
    def visit(self, subgraph, visitor):
        visitor(self)
        for k, v in self.obj.items():
            v.visit(subgraph, visitor)


class Type(Node):
    def __init__(self, name, params=None):
        self.name = name
        self.params = params

    def format(self):
        return self.name + (
            f"<{",".join([p.format() for p in self.params])}>" if self.params else ""
        )

    def index(self, symbols, indexer):
        pass

    def filter(self, subgraph):
        # TODO: type chain collapsing / path compression, e.g.
        #   type Drinks = Juice
        #   type Juice = {"name": "apple"}
        # becomes
        #   type Drinks = {"name": "apple"}
        # if the type is not a generic type parameter
        if not subgraph.is_local(self.name):
            # TODO: BUGBUG: isn't it possible to have two instances of the same generic with different type parameters?
            filtered = subgraph.filtered(self.name)
            if not filtered:
                type = subgraph.original(self.name)
                filtered = type.filter(subgraph)
                subgraph.add(self.name, filtered)
            if isinstance(filtered, Define) and isinstance(filtered.type, Never):
                return Never()
        return self

    def visit(self, subgraph, visitor):
        type = subgraph.filtered(self.name)
        if type:
            type.visit(subgraph, visitor)
        if self.params:
            for p in self.params:
                p.visit(subgraph, visitor)


class Array(Node):
    def __init__(self, type):
        self.type = type

    def format(self):
        return self.type.format() + "[]"

    def index(self, symbols, indexer):
        self.type.index(symbols, indexer)

    def filter(self, nodes):
        t = self.type.filter(nodes)
        return Array(t) if not isinstance(t, Never) else Never()
    
    def visit(self, subgraph, visitor):
        visitor(self)
        self.type.visit(subgraph, visitor)


#
# Builders
#
def build_symbol_table(nodes):
    symbols = SymbolTable()
    for node in nodes:
        if isinstance(node, Define):
            symbols.add(node.name, node)
    return symbols

def build_type_index(type_defs):
    # Build the symbol table for type name references.
    symbols = build_symbol_table(type_defs)

    # Build index of terms mentioned in types.
    indexer = TypeIndex()
    for x in type_defs:
        x.index(symbols, indexer)

    return symbols, indexer

def build_filtered_types(type_defs, symbols, indexer, text):
    # Filter the graph based on search terms
    nodes = indexer.nodes(text)
    subgraph = Subgraph(symbols, nodes)
    filtered = [n.filter(subgraph) for n in type_defs]

    # Collect nodes reachable from the root
    reachable = OrderedDict()
    def visitor(node):
        if isinstance(node, Define):
            reachable[node] = None

    filtered[0].visit(subgraph, visitor)
    return reachable

def collect_string_literals(data):
    """
    Collects all string literal values in a hierarchical dictionary.

    Args:
      data (dict): The hierarchical dictionary to traverse.

    Returns:
      list: A list of all string literal values found in the dictionary.
    """
    literals = []

    def _collect(data):
        if isinstance(data, dict):
            for key, value in data.items():
                _collect(value)
        elif isinstance(data, list):
            for item in data:
                _collect(item)
        elif isinstance(data, str):
            literals.append(data)

    _collect(data)
    return literals

