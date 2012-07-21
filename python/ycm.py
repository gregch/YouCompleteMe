#!/usr/bin/env python
#
# Copyright (C) 2011, 2012  Strahinja Val Markovic  <val@markovic.io>
#
# This file is part of YouCompleteMe.
#
# YouCompleteMe is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# YouCompleteMe is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with YouCompleteMe.  If not, see <http://www.gnu.org/licenses/>.

import re
import vim
import indexer

min_num_chars = int( vim.eval( "g:ycm_min_num_of_chars_for_completion" ) )
clang_filetypes = set( [ 'c', 'cpp', 'objc', 'objcpp' ] )

class Completer( object ):
  def __init__( self ):
    self.future = None

  def AsyncCandidateRequestReady( self ):
    return self.future.ResultsReady()


  def CandidatesFromStoredRequest( self ):
    if not self.future:
      return []
    return self.future.GetResults()


class IdentifierCompleter( Completer ):
  def __init__( self ):
    self.completer = indexer.IdentifierCompleter()
    self.completer.EnableThreading()
    self.pattern = re.compile( r"[_a-zA-Z]\w*" )


  def CandidatesForQueryAsync( self, query ):
    filetype = vim.eval( "&filetype" )
    self.future = self.completer.CandidatesForQueryAndTypeAsync(
      SanitizeQuery( query ),
      filetype )


  def AddIdentifier( self, identifier ):
    filetype = vim.eval( "&filetype" )
    filepath = vim.eval( "expand('%:p')" )

    if not filetype or not filepath or not identifier:
      return

    vector = indexer.StringVec()
    vector.append( identifier )
    self.completer.AddCandidatesToDatabase( vector,
                                            filetype,
                                            filepath,
                                            False )


  def AddPreviousIdentifier( self ):
    self.AddIdentifier( PreviousIdentifier() )


  def AddBufferIdentifiers( self ):
    text = "\n".join( vim.current.buffer )
    text = RemoveIdentFreeText( text )

    idents = re.findall( self.pattern, text )
    filetype = vim.eval( "&filetype" )
    filepath = vim.eval( "expand('%:p')" )

    if not filetype or not filepath:
      return

    vector = indexer.StringVec()
    vector.extend( idents )
    self.completer.AddCandidatesToDatabase( vector,
                                            filetype,
                                            filepath,
                                            True )


class ClangCompleter( Completer ):
  def __init__( self ):
    self.completer = indexer.ClangCompleter()
    self.completer.EnableThreading()
    self.contents_holder = []
    self.filename_holder = []

  def CandidatesForQueryAsync( self, query ):
    # TODO: sanitize query
    files = indexer.UnsavedFileVec()

    # CAREFUL HERE! For UnsavedFile filename and contents we are referring
    # directly to Python-allocated and -managed memory since we are accepting
    # pointers to data members of python objects. We need to ensure that those
    # objects outlive our UnsavedFile objects. This is why we need the
    # contents_holder and filename_holder lists, to make sure the string objects
    # are still around when we call CandidatesForQueryAndLocationInFile.  We do
    # this to avoid an extra copy of the entire file contents.

    if not query:
      self.contents_holder = []
      self.filename_holder = []
      for buffer in GetUnsavedBuffers():
        self.contents_holder.append( '\n'.join( buffer ) )
        self.filename_holder.append( buffer.name )

        unsaved_file = indexer.UnsavedFile()
        unsaved_file.contents_ = self.contents_holder[ -1 ]
        unsaved_file.length_ = len( self.contents_holder[ -1 ] )
        unsaved_file.filename_ = self.filename_holder[ -1 ]

        files.append( unsaved_file )

    line, _ = vim.current.window.cursor
    column = int( vim.eval( "s:completion_start_column" ) ) + 1
    current_buffer = vim.current.buffer
    self.future = self.completer.CandidatesForQueryAndLocationInFileAsync(
      query,
      current_buffer.name,
      line,
      column,
      files )


  def CandidatesFromStoredRequest( self ):
    if not self.future:
      return []
    return [ CompletionDataToDict( x ) for x in self.future.GetResults() ]


def GetUnsavedBuffers():
  def BufferModified( buffer_number ):
    to_eval = 'getbufvar({0}, "&mod")'.format( buffer_number )
    return bool( int( vim.eval( to_eval ) ) )

  return ( x for x in vim.buffers if BufferModified( x.number ) )


def CompletionDataToDict( completion_data ):
  # see :h complete-items for a description of the dictionary fields
  return {
    'word' : completion_data.TextToInsertInBuffer(),
    'abbr' : completion_data.original_string_,
    'menu' : completion_data.extra_menu_info_,
    'kind' : completion_data.kind_,
    # TODO: add detailed_info_ as 'info'
  }


def CurrentColumn():
  """Do NOT access the CurrentColumn in vim.current.line. It doesn't exist yet.
  Only the chars before the current column exist in vim.current.line."""

  # vim's columns are 1-based while vim.current.line columns are 0-based
  # ... but vim.current.window.cursor (which returns a (line, column) tuple)
  # columns are 0-based, while the line from that same tuple is 1-based.
  # vim.buffers buffer objects OTOH have 0-based lines and columns.
  # Pigs have wings and I'm a loopy purple duck. Everything makes sense now.
  return vim.current.window.cursor[ 1 ]


def CurrentLineAndColumn():
  # See the comment in CurrentColumn about the calculation for the line and
  # column number
  line, column = vim.current.window.cursor
  line -= 1
  return line, column


def ShouldUseClang( start_column ):
  filetype = vim.eval( "&filetype" )
  if filetype not in clang_filetypes:
    return False

  line = vim.current.line
  previous_char_index = start_column - 1
  if ( not len( line ) or
       previous_char_index < 0 or
       previous_char_index >= len( line ) ):
    return False

  if line[ previous_char_index ] == '.':
    return True

  if previous_char_index - 1 < 0:
    return False

  two_previous_chars = line[ previous_char_index - 1 : start_column ]
  if ( two_previous_chars == '->' or two_previous_chars == '::' ):
    return True

  return False


def IsIdentifierChar( char ):
  return char.isalnum() or char == '_'


def CompletionStartColumn():
  """Returns the 0-based index where the completion string should start. So if
  the user enters:
    foo.bar^
  with the cursor being at the location of the caret, then the starting column
  would be the index of the letter 'b'.
  """

  line = vim.current.line
  start_column = CurrentColumn()

  while start_column > 0 and IsIdentifierChar( line[ start_column - 1 ] ):
    start_column -= 1
  return start_column


def EscapeForVim( text ):
  return text.replace( "'", "''" )


def PreviousIdentifier():
  line_num, column_num = CurrentLineAndColumn()
  buffer = vim.current.buffer
  line = buffer[ line_num ]

  end_column = column_num

  while end_column > 0 and not IsIdentifierChar( line[ end_column - 1 ] ):
    end_column -= 1

  # Look at the previous line if we reached the end of the current one
  if end_column == 0:
    try:
      line = buffer[ line_num - 1]
    except:
      return ""
    end_column = len( line )
    while end_column > 0 and not IsIdentifierChar( line[ end_column - 1 ] ):
      end_column -= 1
    print end_column, line

  start_column = end_column
  while start_column > 0 and IsIdentifierChar( line[ start_column - 1 ] ):
    start_column -= 1

  if end_column - start_column < min_num_chars:
    return ""

  return line[ start_column : end_column ]


def ShouldAddIdentifier():
  current_column = CurrentColumn()
  previous_char_index = current_column - 1
  if previous_char_index < 0:
    return True
  line = vim.current.line
  try:
    previous_char = line[ previous_char_index ]
  except IndexError:
    return False

  if IsIdentifierChar( previous_char ):
    return False

  if ( not IsIdentifierChar( previous_char ) and
       previous_char_index > 0 and
       IsIdentifierChar( line[ previous_char_index - 1 ] ) ):
    return True
  else:
    return line[ : current_column ].isspace()


def SanitizeQuery( query ):
  return query.strip()


def RemoveIdentFreeText( text ):
  """Removes commented-out code and code in quotes."""

  # TODO: do we still need this sub-func?
  def replacer( match ):
    s = match.group( 0 )
    if s.startswith( '/' ):
      return ""
    else:
      return s

  pattern = re.compile(
    r'//.*?$|#.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
    re.DOTALL | re.MULTILINE )

  return re.sub( pattern, replacer, text )

