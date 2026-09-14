"""Python, the first language -- and nothing more than a configuration of node types.

The point of this file is how short it is. If adding a language needed more than a grammar
module, a node-type map and possibly a ``qualname`` override, the seam in
:mod:`relore.code.api` would not be real.
"""

from __future__ import annotations

from relore.code.providers.treesitter import TreeSitterProvider


class PythonProvider(TreeSitterProvider):
    name = "python"
    patterns = ("*.py", "*.pyi")
    grammar_module = "tree_sitter_python"
    definition_nodes = {"class_definition": "class", "function_definition": "function"}
    #: Every way a name is written, **labelled** -- not only names in a call position.
    #:
    #: Calls alone was a reasoned choice ("a graph that counts every mention of ``self``
    #: ranks nothing"), and a measurement overturned it: ``refs
    #: compute_default_rope_parameters`` returned 21 of ~400 occurrences in
    #: ``huggingface/transformers`` and missed
    #: ``rope_init_fn: Callable = self.compute_default_rope_parameters`` -- a value read
    #: through an attribute rather than called, in the very file being debugged. A sweep
    #: for "every affected call site" built on that under-reports by 95% and looks
    #: complete. The ranking worry is answered by the ``kind`` rather than by omission:
    #: core groups the counts, and ``map`` weighs a name against how many definitions
    #: share it.
    #:
    #: The attribute chain in front of a name is still not resolved -- that is the
    #: disambiguation tier and it needs a whole tree in scope -- so ``a.b.foo()`` reports
    #: ``foo`` and the bare name is what matches. A definition is reported too, because
    #: "which files define their own copy of this function" is what a repository that
    #: duplicates model code is actually asked, and the kind says which rows those are.
    reference_nodes = {
        "call": ("call", "function"),
        "attribute": ("attribute", "attribute"),
        "function_definition": ("definition", "name"),
        "class_definition": ("definition", "name"),
        "identifier": ("name", None),
    }
    reference_precedence = ("call", "definition", "attribute", "name")

    def kind_for(self, kind: str, parents: tuple[str, ...]) -> str:
        """A function inside anything is a method, which is what a reader expects to see
        next to ``Gemma3Model.forward``."""
        return "method" if kind == "function" and parents else kind

    def _reference_name(self, node, source: bytes) -> str:
        """The callee of a call, as written.

        ``foo()`` gives ``foo``; ``a.b.foo()`` gives ``foo``, because the attribute chain
        in front of it is a value this provider cannot resolve, and a *name* is what
        section 1 needs -- resolving a mention to ``Gemma3Model.forward`` is the
        disambiguation tier, and it belongs with the daemon's lens where a whole tree is
        in scope, not with a pure function of one file.
        """
        function = node.child_by_field_name("function")
        if function is None:
            return ""
        if function.type == "attribute":
            attribute = function.child_by_field_name("attribute")
            function = attribute if attribute is not None else function
        return source[function.start_byte : function.end_byte].decode("utf-8", "replace")
