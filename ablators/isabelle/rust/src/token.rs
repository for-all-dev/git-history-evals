//! Outer-syntax tokens (mirrors `Pure/Isar/token.scala` `Token` / `Token.Kind`).
//! The tokenizer that *produces* these lives in `tokenize` (symbol + scan).

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Command,
    Keyword,
    Ident,
    LongIdent,
    SymIdent,
    Var,
    TypeIdent,
    TypeVar,
    Nat,
    Float,
    Space,
    String,
    AltString,
    Cartouche,
    Control,
    InformalComment,
    FormalComment,
    Error,
    Unparsed,
}

#[derive(Clone, PartialEq, Eq, Debug)]
pub struct Token {
    pub kind: Kind,
    pub source: String,
}

impl Token {
    pub fn new(kind: Kind, source: impl Into<String>) -> Token {
        Token {
            kind,
            source: source.into(),
        }
    }

    pub fn is_command(&self) -> bool {
        self.kind == Kind::Command
    }
    pub fn is_keyword(&self) -> bool {
        self.kind == Kind::Keyword
    }
    pub fn is_keyword_str(&self, name: &str) -> bool {
        self.kind == Kind::Keyword && self.source == name
    }
    pub fn is_ident(&self) -> bool {
        self.kind == Kind::Ident
    }
    pub fn is_long_ident(&self) -> bool {
        self.kind == Kind::LongIdent
    }
    pub fn is_space(&self) -> bool {
        self.kind == Kind::Space
    }
    pub fn is_informal_comment(&self) -> bool {
        self.kind == Kind::InformalComment
    }
    pub fn is_formal_comment(&self) -> bool {
        self.kind == Kind::FormalComment
    }
    pub fn is_comment(&self) -> bool {
        self.is_informal_comment() || self.is_formal_comment()
    }
    pub fn is_error(&self) -> bool {
        self.kind == Kind::Error
    }
    /// `is_ignored` = whitespace or informal comment (Pure/Isar/token.scala).
    pub fn is_ignored(&self) -> bool {
        self.is_space() || self.is_informal_comment()
    }
    /// `is_proper` = not whitespace, not a comment.
    pub fn is_proper(&self) -> bool {
        !self.is_space() && !self.is_comment()
    }
    /// `is_name` = IDENT | LONG_IDENT | SYM_IDENT | STRING | NAT.
    pub fn is_name(&self) -> bool {
        matches!(
            self.kind,
            Kind::Ident | Kind::LongIdent | Kind::SymIdent | Kind::String | Kind::Nat
        )
    }
}

/// `Token.implode`: concatenate token sources (lossless).
pub fn implode(content: &[Token]) -> String {
    let mut s = String::new();
    for t in content {
        s.push_str(&t.source);
    }
    s
}
