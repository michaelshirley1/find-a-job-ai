import { Routes, Route, Navigate } from 'react-router-dom';

import Home from './pages/home';
import Search from './pages/search';
import Pdf from './pages/pdf';

export default function Routing() {
    return (
        <Routes>
            <Route index element={<Home />}/>
            <Route path="search" element={<Search/>}/>
            <Route path="pdf" element={<Pdf/>}/>
        </Routes>
    )
}