
from datalayer.node.node_hash import HashNode
from datalayer.errors import NodeAlreadyExistsError

# Trie hash node class
# code adapted from https://www.geeksforgeeks.org/trie-insert-and-search/
class TrieHashNode:
     
    def __init__(self, alphalen, hash_node: HashNode=None):
        """Default constructor.
        
        Arguments:
        alphalen    -- length of the alphabet stored in the trie
        hash_node   -- hash node associated to the trie node
        """
        self._children  = [None for _ in range(alphalen)]
        # _hash_node not None if this node represent the end of the "wor(l)d"
        # for us, the word will be a hash (the hash corresponding to _hash_node) 
        self._hash_node = hash_node

    def __str__(self):
        if self._hash_node is None:
            return "TrieHashNode with hash_node None"
        else:
            return "TrieHashNode ID: " + str(self._hash_node.get_id()) + ", Neighbors: " + self._hash_node.print_neighbors()

    def __repr__(self): # for printing while iterating TrieHashNode data structures
        return "<" + str(self) + ">"